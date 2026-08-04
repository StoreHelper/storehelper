from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import httpx
from typer.testing import CliRunner

import storehelper.cli as cli_module
from storehelper.credentials.models import HonorApiCredential, HuaweiServiceAccount
from storehelper.credentials.providers import MemoryKeyring
from storehelper.runs.repository import RunRepository
from storehelper.stores.models import CredentialKind

runner = CliRunner()


def _project(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "storehelper.yaml"
    config.write_text(
        """version: 1
apps:
  wallet:
    package_name: com.example.wallet
    stores:
      honor:
        credential_profile: release
        version_code: 43
        language: zh-CN
""",
        encoding="utf-8",
    )
    apk = tmp_path / "wallet.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
        archive.writestr("assets/payload.bin", b"payload" * 1024)
    return config, apk


def _keyring() -> MemoryKeyring:
    keyring = MemoryKeyring()
    credential = HonorApiCredential(
        client_id="known-honor-client",
        client_secret="known-honor-secret",
    )
    keyring.set("release", credential.to_storage_json(), CredentialKind.HONOR_API)
    return keyring


class HonorCliBackend:
    def __init__(self) -> None:
        self.uploaded_sha: str | None = None
        self.bound_sha: str | None = None
        self.notes = "旧版本说明"
        self.audit_result = 1
        self.current_version = 42
        self.release_id = "release-42"
        self.fail_bind_once = False
        self.bind_calls = 0
        self.submit_calls = 0

    def _detail(self) -> dict[str, object]:
        files = []
        if self.bound_sha is not None:
            files.append({"fileType": 100, "fileSha256": self.bound_sha})
        return {
            "basicInfo": {"appId": 123456, "packageName": "com.example.wallet"},
            "languageInfo": [
                {
                    "languageId": "zh-CN",
                    "appName": "示例钱包",
                    "intro": "应用介绍",
                    "briefIntro": "应用简介",
                    "newFeature": self.notes,
                }
            ],
            "fileInfo": files,
            "releaseInfo": {"versionCode": 42},
        }

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        if request.url.host == "iam.developer.honor.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "known-honor-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        if request.url.path.endswith("/get-app-id"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [{"appId": 123456, "packageName": "com.example.wallet"}],
                },
            )
        if request.url.path.endswith("/get-app-detail"):
            return httpx.Response(200, json={"code": 0, "data": self._detail()})
        if request.url.path.endswith("/get-app-current-release"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "appId": 123456,
                        "releaseId": self.release_id,
                        "versionCode": self.current_version,
                        "auditResult": self.audit_result,
                    },
                },
            )
        if request.url.path.endswith("/get-file-upload-url"):
            payload = json.loads(request.content)[0]
            self.uploaded_sha = payload["fileSha256"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [
                        {
                            "fileName": payload["fileName"],
                            "uploadUrl": "https://example.invalid/ignored",
                            "objectId": 987654321,
                            "expireTime": 4_000_000_000,
                        }
                    ],
                },
            )
        if request.url.path.endswith("/file-upload"):
            return httpx.Response(200, json={"code": 0})
        if request.url.path.endswith("/update-file-info"):
            self.bind_calls += 1
            self.bound_sha = self.uploaded_sha
            self.audit_result = 4
            self.current_version = 43
            self.release_id = "draft-43"
            if self.fail_bind_once:
                self.fail_bind_once = False
                raise httpx.ReadError("lost bind response", request=request)
            return httpx.Response(200, json={"code": 0})
        if request.url.path.endswith("/update-language-info"):
            self.notes = json.loads(request.content)["languageInfoList"][0]["newFeature"]
            return httpx.Response(200, json={"code": 0})
        if request.url.path.endswith("/submit-audit"):
            self.submit_calls += 1
            self.audit_result = 0
            self.release_id = "review-43"
            return httpx.Response(200, json={"code": 0, "data": "review-43"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


def _install_backend(tmp_path: Path, monkeypatch, backend: HonorCliBackend) -> None:
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(cli_module, "KEYRING", _keyring())
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(backend)),
    )


def test_honor_help_config_dry_run_and_no_submit_are_safe(tmp_path: Path, monkeypatch) -> None:
    config, apk = _project(tmp_path)
    monkeypatch.setattr(cli_module, "KEYRING", MemoryKeyring())
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("network client constructed")),
    )

    help_result = runner.invoke(cli_module.app, ["publish", "--help"])
    validated = runner.invoke(
        cli_module.app,
        ["config", "validate", "--config", str(config), "--output", "json"],
    )
    dry_run = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--store",
            "honor",
            "--file",
            str(apk),
            "--dry-run",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )
    no_submit = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--store",
            "honor",
            "--file",
            str(apk),
            "--no-submit",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert help_result.exit_code == 0
    assert "honor" in "".join(help_result.stdout.split())
    assert validated.exit_code == 0
    assert json.loads(validated.stdout) == {"ok": True, "valid": True}
    assert dry_run.exit_code == 0
    assert json.loads(dry_run.stdout)["stage"] == "completed"
    assert no_submit.exit_code == 2
    assert json.loads(no_submit.stdout)["code"] == "NO_SUBMIT_UNSUPPORTED"


def test_honor_cli_verify_publish_status_and_redacted_receipt(tmp_path: Path, monkeypatch) -> None:
    config, apk = _project(tmp_path)
    backend = HonorCliBackend()
    _install_backend(tmp_path, monkeypatch, backend)

    verified = runner.invoke(
        cli_module.app,
        [
            "credentials",
            "verify",
            "--store",
            "honor",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )
    published = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--store",
            "honor",
            "--file",
            str(apk),
            "--release-notes",
            "修复已知问题",
            "--yes",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )
    status = runner.invoke(
        cli_module.app,
        ["status", "--store", "honor", "--output", "json", "--config", str(config)],
    )

    assert verified.exit_code == 0, verified.stdout
    assert json.loads(verified.stdout)["stage"] == "app_verified"
    assert published.exit_code == 0, published.stdout
    assert json.loads(published.stdout)["stage"] == "submitted"
    assert status.exit_code == 0, status.stdout
    assert "in_review" in json.loads(status.stdout)["message"]
    receipt = RunRepository(tmp_path / "runs").list()[0]
    assert receipt.state.value == "completed"
    assert receipt.operation_id == "123456"
    assert receipt.artifact_id == f"987654321:{hashlib.sha256(apk.read_bytes()).hexdigest()}"
    assert receipt.submission_id == "review-43"
    rendered = verified.stdout + published.stdout + status.stdout + receipt.model_dump_json()
    for secret in ("known-honor-client", "known-honor-secret", "known-honor-token"):
        assert secret not in rendered


def test_honor_cli_resumes_after_ambiguous_binding_without_replay(
    tmp_path: Path, monkeypatch
) -> None:
    config, apk = _project(tmp_path)
    backend = HonorCliBackend()
    backend.fail_bind_once = True
    _install_backend(tmp_path, monkeypatch, backend)

    first = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--store",
            "honor",
            "--file",
            str(apk),
            "--release-notes",
            "修复已知问题",
            "--yes",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )
    run_id = json.loads(first.stdout)["run_id"]
    resumed = runner.invoke(
        cli_module.app,
        ["resume", run_id, "--output", "json", "--config", str(config)],
    )

    assert first.exit_code == 6, first.stdout
    assert json.loads(first.stdout)["resumable"] is True
    assert resumed.exit_code == 0, resumed.stdout
    assert json.loads(resumed.stdout)["stage"] == "submitted"
    assert backend.bind_calls == 1
    assert backend.submit_calls == 1


def test_honor_wrong_credential_kind_is_rejected_before_network(
    tmp_path: Path, rsa_private_key: str, monkeypatch
) -> None:
    config, _ = _project(tmp_path)
    keyring = MemoryKeyring()
    wrong = HuaweiServiceAccount(
        key_id="huawei-key",
        sub_account="huawei-sub",
        private_key=rsa_private_key,
    )
    keyring.set("release", wrong.to_storage_json(), CredentialKind.HONOR_API)
    monkeypatch.setattr(cli_module, "KEYRING", keyring)
    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("network client constructed")),
    )

    result = runner.invoke(
        cli_module.app,
        [
            "credentials",
            "verify",
            "--store",
            "honor",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 3
    assert json.loads(result.stdout)["code"] == "CREDENTIAL_KIND_MISMATCH"
