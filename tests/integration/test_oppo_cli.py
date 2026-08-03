from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx
from typer.testing import CliRunner

import storehelper.cli as cli_module
from storehelper.credentials.models import HuaweiServiceAccount, OppoApiCredential
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
      oppo:
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
    return config, apk


def _application(audit_status: int = 111) -> dict[str, object]:
    return {
        "pkg_name": "com.example.wallet",
        "version_code": "42",
        "audit_status": audit_status,
        "app_name": "Example Wallet",
        "second_category_id": "463",
        "third_category_id": "6648",
        "summary": "Safe payments",
        "detail_desc": "A complete existing application description.",
        "privacy_source_url": "https://example.com/privacy",
        "icon_url": "https://cdn.example.com/icon.png",
        "pic_url": "https://cdn.example.com/one.png",
        "age_level": "18",
        "adaptive_equipment": "4",
        "copyright_url": "https://cdn.example.com/copyright.pdf",
        "business_username": "Release Owner",
        "business_email": "release@example.com",
        "business_mobile": "13800138000",
    }


def _keyring() -> MemoryKeyring:
    keyring = MemoryKeyring()
    credential = OppoApiCredential(
        client_id="known-oppo-client",
        client_secret="known-oppo-secret",
    )
    keyring.set("release", credential.to_storage_json(), CredentialKind.OPPO_API)
    return keyring


class OppoBackend:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.audit_status = 111

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path == "/developer/v1/token":
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {"access_token": "known-oppo-token", "expire_in": 7200},
                },
            )
        if request.url.path == "/resource/v1/app/info":
            return httpx.Response(
                200,
                json={"errno": 0, "data": _application(self.audit_status)},
            )
        if request.url.path == "/resource/v1/upload/get-upload-url":
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {
                        "upload_url": "https://upload.oppomobile.com/file",
                        "sign": "known-upload-sign",
                    },
                },
            )
        if request.url.host == "upload.oppomobile.com":
            await request.aread()
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {"url": "https://cdn.oppomobile.com/known-private-file.apk"},
                },
            )
        await request.aread()
        return httpx.Response(200, json={"errno": 0, "data": {"task_id": "task-1"}})


def test_publish_help_and_config_validation_expose_oppo(tmp_path: Path) -> None:
    config, _ = _project(tmp_path)

    help_result = runner.invoke(cli_module.app, ["publish", "--help"])
    validated = runner.invoke(
        cli_module.app,
        ["config", "validate", "--config", str(config), "--output", "json"],
    )

    assert help_result.exit_code == 0
    assert "oppo" in "".join(help_result.stdout.split())
    assert validated.exit_code == 0
    assert json.loads(validated.stdout) == {"ok": True, "valid": True}


def test_oppo_no_submit_is_rejected_before_credentials_network_or_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, apk = _project(tmp_path)
    monkeypatch.setattr(cli_module, "KEYRING", MemoryKeyring())
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("network client constructed")),
    )

    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--store",
            "oppo",
            "--file",
            str(apk),
            "--no-submit",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["code"] == "NO_SUBMIT_UNSUPPORTED"
    assert RunRepository(tmp_path / "runs").list() == []


def test_oppo_dry_run_stays_offline_without_credentials(tmp_path: Path, monkeypatch) -> None:
    config, apk = _project(tmp_path)
    monkeypatch.setattr(cli_module, "KEYRING", MemoryKeyring())
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("network client constructed")),
    )

    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--store",
            "oppo",
            "--file",
            str(apk),
            "--dry-run",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["stage"] == "completed"


def test_oppo_publish_requires_confirmation_and_release_notes(tmp_path: Path) -> None:
    config, apk = _project(tmp_path)
    common = [
        "publish",
        "--store",
        "oppo",
        "--file",
        str(apk),
        "--output",
        "json",
        "--config",
        str(config),
    ]

    unconfirmed = runner.invoke(cli_module.app, [*common, "--release-notes", "Fixes"])
    missing_notes = runner.invoke(cli_module.app, [*common, "--yes"])

    assert unconfirmed.exit_code == 2
    assert json.loads(unconfirmed.stdout)["code"] == "PUBLISH_CONFIRMATION_REQUIRED"
    assert missing_notes.exit_code == 2
    assert json.loads(missing_notes.stdout)["code"] == "RELEASE_NOTES_MISSING"


def test_oppo_publish_verify_and_status_use_registered_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, apk = _project(tmp_path)
    backend = OppoBackend()
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(cli_module, "KEYRING", _keyring())
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(backend)),
    )

    published = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--store",
            "oppo",
            "--file",
            str(apk),
            "--release-notes",
            "Fixes",
            "--yes",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )
    verified = runner.invoke(
        cli_module.app,
        [
            "credentials",
            "verify",
            "--store",
            "oppo",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )
    status = runner.invoke(
        cli_module.app,
        ["status", "--store", "oppo", "--output", "json", "--config", str(config)],
    )

    assert published.exit_code == 0
    assert json.loads(published.stdout)["stage"] == "submitted"
    assert verified.exit_code == 0
    assert json.loads(verified.stdout)["stage"] == "app_verified"
    assert status.exit_code == 0
    assert "approved" in json.loads(status.stdout)["message"]
    receipt = RunRepository(tmp_path / "runs").list()[0]
    assert receipt.state.value == "completed"
    assert receipt.submission_id == "com.example.wallet"
    rendered = published.stdout + verified.stdout + status.stdout + receipt.model_dump_json()
    for secret in (
        "known-oppo-client",
        "known-oppo-secret",
        "known-oppo-token",
        "known-upload-sign",
        "known-private-file.apk",
    ):
        assert secret not in rendered


def test_oppo_wrong_credential_kind_is_rejected_before_network(
    tmp_path: Path,
    rsa_private_key: str,
    monkeypatch,
) -> None:
    config, _ = _project(tmp_path)
    keyring = MemoryKeyring()
    wrong = HuaweiServiceAccount(
        key_id="huawei-key",
        sub_account="huawei-sub",
        private_key=rsa_private_key,
    )
    keyring.set("release", wrong.to_storage_json(), CredentialKind.OPPO_API)
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
            "oppo",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 3
    assert json.loads(result.stdout)["code"] == "CREDENTIAL_KIND_MISMATCH"
