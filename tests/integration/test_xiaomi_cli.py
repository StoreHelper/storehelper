from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx
from typer.testing import CliRunner

import storehelper.cli as cli_module
from storehelper.credentials.models import XiaomiApiCredential
from storehelper.credentials.providers import MemoryKeyring
from storehelper.runs.repository import RunRepository

runner = CliRunner()


def _project(tmp_path: Path) -> tuple[Path, Path]:
    icon = tmp_path / "assets" / "xiaomi.png"
    icon.parent.mkdir()
    icon.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01")
    config = tmp_path / "storehelper.yaml"
    config.write_text(
        """version: 1
apps:
  wallet:
    package_name: com.example.wallet
    stores:
      xiaomi:
        credential_profile: release
        app_name: Example Wallet
        icon: assets/xiaomi.png
        privacy_url: https://example.com/privacy
        language: zh-CN
""",
        encoding="utf-8",
    )
    apk = tmp_path / "wallet.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
    return config, apk


def _keyring(certificate: str) -> MemoryKeyring:
    credential = XiaomiApiCredential(
        username="developer@example.com",
        api_secret="api-secret",
        public_key_certificate=certificate,
    )
    keyring = MemoryKeyring()
    keyring.set("release", credential.to_storage_json(), cli_module.CredentialKind.XIAOMI_API)
    return keyring


def test_publish_help_exposes_xiaomi_choice() -> None:
    result = runner.invoke(cli_module.app, ["publish", "--help"])

    assert result.exit_code == 0
    assert "xiaomi" in "".join(result.stdout.split())


def test_xiaomi_no_submit_is_rejected_before_credentials_network_or_receipt(
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
            "xiaomi",
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


def test_xiaomi_status_is_rejected_before_credentials_or_network(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, _ = _project(tmp_path)
    monkeypatch.setattr(cli_module, "KEYRING", MemoryKeyring())
    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("network client constructed")),
    )

    result = runner.invoke(
        cli_module.app,
        [
            "status",
            "--store",
            "xiaomi",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["code"] == "STORE_STATUS_UNSUPPORTED"


def test_xiaomi_dry_run_stays_offline_without_credentials(tmp_path: Path, monkeypatch) -> None:
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
            "xiaomi",
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


def test_xiaomi_publish_requires_confirmation_and_release_notes(
    tmp_path: Path,
) -> None:
    config, apk = _project(tmp_path)

    unconfirmed = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--store",
            "xiaomi",
            "--file",
            str(apk),
            "--release-notes",
            "Fixes",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )
    missing_notes = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--store",
            "xiaomi",
            "--file",
            str(apk),
            "--yes",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert unconfirmed.exit_code == 2
    assert json.loads(unconfirmed.stdout)["code"] == "PUBLISH_CONFIRMATION_REQUIRED"
    assert missing_notes.exit_code == 2
    assert json.loads(missing_notes.stdout)["code"] == "RELEASE_NOTES_MISSING"


def test_xiaomi_full_publish_and_credential_verify_use_registered_adapter(
    tmp_path: Path,
    rsa_public_certificate: str,
    monkeypatch,
) -> None:
    config, apk = _project(tmp_path)
    monkeypatch.setattr(cli_module, "KEYRING", _keyring(rsa_public_certificate))
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")
    requests: list[str] = []
    real_async_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/dev/query"):
            return httpx.Response(
                200,
                json={
                    "result": 0,
                    "updateVersion": True,
                    "packageInfo": {
                        "appName": "Example Wallet",
                        "packageName": "com.example.wallet",
                    },
                },
            )
        await request.aread()
        return httpx.Response(200, json={"result": 0})

    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(handler)),
    )
    published = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--store",
            "xiaomi",
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
            "xiaomi",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert published.exit_code == 0
    assert json.loads(published.stdout)["stage"] == "submitted"
    assert verified.exit_code == 0
    assert json.loads(verified.stdout)["stage"] == "app_verified"
    assert requests == [
        "/devupload/dev/query",
        "/devupload/dev/push",
        "/devupload/dev/query",
    ]
    receipt = RunRepository(tmp_path / "runs").list()[0]
    assert receipt.state.value == "completed"
    assert receipt.submission_id == "com.example.wallet"
    assert "api-secret" not in published.stdout + verified.stdout + receipt.model_dump_json()
