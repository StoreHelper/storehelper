from __future__ import annotations

import json
import zipfile
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from typer.testing import CliRunner

import storehelper.cli as cli_module
from storehelper.credentials.models import HuaweiServiceAccount, VivoApiCredential
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
      vivo:
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


def _keyring() -> MemoryKeyring:
    keyring = MemoryKeyring()
    credential = VivoApiCredential(
        access_key="known-vivo-access",
        secret_key="known-vivo-secret",
    )
    keyring.set("release", credential.to_storage_json(), CredentialKind.VIVO_API)
    return keyring


class VivoBackend:
    def __init__(self) -> None:
        self.methods: list[str] = []
        self.status = 5

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers["content-type"].startswith("multipart/form-data"):
            await request.aread()
            self.methods.append("app.upload.apk.app.64")
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "serialnumber": "known-vivo-serial",
                    },
                },
            )
        form = {
            key: values[0] for key, values in parse_qs((await request.aread()).decode()).items()
        }
        method = form["method"]
        self.methods.append(method)
        if method == "app.query.details":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "packageName": "com.example.wallet",
                        "versionCode": 42,
                        "status": self.status,
                    },
                },
            )
        return httpx.Response(200, json={"code": 0, "data": {}})


def test_publish_help_and_config_validation_expose_vivo(tmp_path: Path) -> None:
    config, _ = _project(tmp_path)

    help_result = runner.invoke(cli_module.app, ["publish", "--help"])
    validated = runner.invoke(
        cli_module.app,
        ["config", "validate", "--config", str(config), "--output", "json"],
    )

    assert help_result.exit_code == 0
    assert "vivo" in "".join(help_result.stdout.split())
    assert validated.exit_code == 0
    assert json.loads(validated.stdout) == {"ok": True, "valid": True}


def test_vivo_no_submit_is_rejected_before_credentials_network_or_receipt(
    tmp_path: Path, monkeypatch
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
            "vivo",
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


def test_vivo_dry_run_stays_offline_without_credentials(tmp_path: Path, monkeypatch) -> None:
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
            "vivo",
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


def test_vivo_publish_requires_confirmation_and_release_notes(tmp_path: Path) -> None:
    config, apk = _project(tmp_path)
    common = [
        "publish",
        "--store",
        "vivo",
        "--file",
        str(apk),
        "--output",
        "json",
        "--config",
        str(config),
    ]

    unconfirmed = runner.invoke(cli_module.app, [*common, "--release-notes", "修复若干问题"])
    missing_notes = runner.invoke(cli_module.app, [*common, "--yes"])

    assert unconfirmed.exit_code == 2
    assert json.loads(unconfirmed.stdout)["code"] == "PUBLISH_CONFIRMATION_REQUIRED"
    assert missing_notes.exit_code == 2
    assert json.loads(missing_notes.stdout)["code"] == "RELEASE_NOTES_MISSING"


def test_vivo_publish_verify_and_status_use_registered_adapter(tmp_path: Path, monkeypatch) -> None:
    config, apk = _project(tmp_path)
    backend = VivoBackend()
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
            "vivo",
            "--file",
            str(apk),
            "--release-notes",
            "修复若干问题",
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
            "vivo",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )
    status = runner.invoke(
        cli_module.app,
        ["status", "--store", "vivo", "--output", "json", "--config", str(config)],
    )

    assert published.exit_code == 0, published.stdout
    assert json.loads(published.stdout)["stage"] == "submitted"
    assert verified.exit_code == 0
    assert json.loads(verified.stdout)["stage"] == "app_verified"
    assert status.exit_code == 0
    assert "approved" in json.loads(status.stdout)["message"]
    receipt = RunRepository(tmp_path / "runs").list()[0]
    assert receipt.state.value == "completed"
    assert receipt.submission_id == "com.example.wallet"
    rendered = published.stdout + verified.stdout + status.stdout + receipt.model_dump_json()
    for secret in ("known-vivo-access", "known-vivo-secret", "known-vivo-serial"):
        assert secret not in rendered


def test_vivo_wrong_credential_kind_is_rejected_before_network(
    tmp_path: Path, rsa_private_key: str, monkeypatch
) -> None:
    config, _ = _project(tmp_path)
    keyring = MemoryKeyring()
    wrong = HuaweiServiceAccount(
        key_id="huawei-key",
        sub_account="huawei-sub",
        private_key=rsa_private_key,
    )
    keyring.set("release", wrong.to_storage_json(), CredentialKind.VIVO_API)
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
            "vivo",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 3
    assert json.loads(result.stdout)["code"] == "CREDENTIAL_KIND_MISMATCH"
