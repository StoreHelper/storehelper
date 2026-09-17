"""Minimal release configuration works without copying package identity into YAML."""

from __future__ import annotations

import json
import plistlib
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

import storehelper.cli as cli
from storehelper.config.loader import ConfigError, load_config
from storehelper.domain.models import OperationResult, PublishStage
from storehelper.publishing.service import PublishingError
from storehelper.runs.repository import RunRepository
from storehelper.stores.models import StoreName


def _varint(value: int) -> bytes:
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _string(field: int, value: str) -> bytes:
    encoded = value.encode()
    return _varint(field << 3 | 2) + _varint(len(encoded)) + encoded


def _message(field: int, value: bytes) -> bytes:
    return _varint(field << 3 | 2) + _varint(len(value)) + value


def _aab(tmp_path: Path, *, package_name: str = "com.example.release", code: int = 42) -> Path:
    android_ns = "http://schemas.android.com/apk/res/android"
    package = _string(2, "package") + _string(3, package_name)
    version = _string(1, android_ns) + _string(2, "versionCode") + _string(3, str(code))
    element = _string(3, "manifest") + _message(4, package) + _message(4, version)
    path = tmp_path / "release.aab"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("BundleConfig.pb", b"config")
        archive.writestr("base/manifest/AndroidManifest.xml", _message(1, element))
    return path


def _config(tmp_path: Path, *, package_name: str | None = None) -> Path:
    path = tmp_path / "storehelper.yaml"
    extra = f"    package_name: {package_name}\n" if package_name else ""
    path.write_text(
        "version: 1\napps:\n  release:\n"
        + extra
        + "    stores:\n      huawei:\n"
        + "        app_id: '123'\n        credential_profile: release\n",
        encoding="utf-8",
    )
    return path


def test_minimal_config_publish_dry_run_extracts_aab_identity(tmp_path: Path) -> None:
    config = _config(tmp_path)
    package = _aab(tmp_path)
    result = CliRunner().invoke(
        cli.app,
        [
            "publish",
            "--file",
            str(package),
            "--dry-run",
            "--config",
            str(config),
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["stage"] == "completed"


def test_minimal_harmony_config_publish_dry_run_extracts_app_identity(tmp_path: Path) -> None:
    config = tmp_path / "storehelper.yaml"
    config.write_text(
        "version: 1\napps:\n  release:\n    stores:\n      harmonyos:\n"
        "        app_id: '234'\n        credential_profile: release\n",
        encoding="utf-8",
    )
    package = tmp_path / "release.app"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "pack.info",
            json.dumps(
                {
                    "summary": {
                        "app": {
                            "bundleName": "com.example.harmony",
                            "version": {"code": 42, "name": "1.2"},
                        }
                    }
                }
            ),
        )
        archive.writestr("entry-default.hap", b"opaque")
    result = CliRunner().invoke(
        cli.app,
        [
            "publish",
            "--store",
            "harmonyos",
            "--file",
            str(package),
            "--dry-run",
            "--config",
            str(config),
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0, result.stdout or result.stderr


def test_minimal_apple_config_publish_dry_run_extracts_bundle_id(tmp_path: Path) -> None:
    config = tmp_path / "storehelper.yaml"
    config.write_text(
        "version: 1\napps:\n  release:\n    stores:\n      apple:\n"
        "        app_id: '345'\n        app_store_version_id: version-resource\n"
        "        credential_profile: release\n",
        encoding="utf-8",
    )
    package = tmp_path / "release.ipa"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "Payload/Release.app/Info.plist",
            plistlib.dumps(
                {
                    "CFBundleIdentifier": "com.example.my-app",
                    "CFBundleShortVersionString": "1.2.3",
                    "CFBundleVersion": "42",
                }
            ),
        )
        archive.writestr("Payload/Release.app/Release", b"binary")
    result = CliRunner().invoke(
        cli.app,
        [
            "publish",
            "--store",
            "apple",
            "--file",
            str(package),
            "--dry-run",
            "--config",
            str(config),
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0, result.stdout or result.stderr


@pytest.mark.asyncio
async def test_package_mismatch_fails_before_credentials_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, package_name="com.example.expected")
    package = _aab(tmp_path, package_name="com.example.actual")

    class NoCredentials:
        def __init__(self, keyring: object) -> None:
            raise AssertionError("credentials accessed before artifact binding")

    monkeypatch.setattr(cli, "CredentialProvider", NoCredentials)
    with pytest.raises(ConfigError) as raised:
        await cli._publish_operation(
            request=cli.PublishRequest(app_alias="release", file=package, dry_run=False),
            config_path=config,
            app_alias="release",
            interactive=False,
        )
    assert raised.value.code == "CONFIG_ARTIFACT_MISMATCH"


def test_status_and_verify_accept_file_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _aab(tmp_path)
    seen: list[Path | None] = []

    async def fake_status(**kwargs: object) -> OperationResult:
        seen.append(kwargs["file"])
        return OperationResult.success(
            store=StoreName.HUAWEI, stage=PublishStage.COMPLETED, run_id=None
        )

    async def fake_verify(**kwargs: object) -> OperationResult:
        seen.append(kwargs["file"])
        return OperationResult.success(
            store=StoreName.HUAWEI, stage=PublishStage.APP_VERIFIED, run_id=None
        )

    monkeypatch.setattr(cli, "_status_operation", fake_status)
    monkeypatch.setattr(cli, "_verify_credentials_operation", fake_verify)
    runner = CliRunner()
    status = runner.invoke(cli.app, ["status", "--file", str(package)])
    verified = runner.invoke(cli.app, ["credentials", "verify", "--file", str(package)])
    assert status.exit_code == verified.exit_code == 0
    assert seen == [package, package]


def test_readonly_target_uses_file_then_unique_receipt(tmp_path: Path) -> None:
    config = load_config(_config(tmp_path))
    application = config.apps["release"]
    package = _aab(tmp_path)
    repo = RunRepository(tmp_path / "runs")
    with_file = cli._resolve_readonly_target(
        application, StoreName.HUAWEI, "release", file=package, repository=repo
    )
    assert with_file.package_name == "com.example.release"
    repo.create(
        store=StoreName.HUAWEI,
        app_alias="release",
        app_id="123",
        package_name="com.example.release",
        package_path=str(package),
        package_sha256="a" * 64,
        logical_name="release.aab",
        language="zh-CN",
        release_notes=None,
        submit=True,
    )
    from_receipt = cli._resolve_readonly_target(
        application, StoreName.HUAWEI, "release", repository=repo
    )
    assert from_receipt.package_name == "com.example.release"


def test_readonly_target_rejects_conflicting_receipts(tmp_path: Path) -> None:
    application = load_config(_config(tmp_path)).apps["release"]
    repo = RunRepository(tmp_path / "runs")
    for name in ("com.example.one", "com.example.two"):
        repo.create(
            store=StoreName.HUAWEI,
            app_alias="release",
            app_id="123",
            package_name=name,
            package_path=str(tmp_path / "release.aab"),
            package_sha256="a" * 64,
            logical_name="release.aab",
            language="zh-CN",
            release_notes=None,
            submit=True,
        )
    with pytest.raises(ConfigError, match="--file"):
        cli._resolve_readonly_target(application, StoreName.HUAWEI, "release", repository=repo)


@pytest.mark.asyncio
async def test_resume_detects_changed_artifact_before_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    package = _aab(tmp_path)
    repo = RunRepository(tmp_path / "runs")
    receipt = repo.create(
        store=StoreName.HUAWEI,
        app_alias="release",
        app_id="123",
        package_name="com.example.release",
        package_path=str(package),
        package_sha256="a" * 64,
        logical_name="release.aab",
        language="zh-CN",
        release_notes=None,
        submit=True,
    )
    monkeypatch.setattr(cli, "RUNS_ROOT", tmp_path / "runs")

    class NoCredentials:
        def __init__(self, keyring: object) -> None:
            raise AssertionError("credentials accessed before saved SHA-256 check")

    monkeypatch.setattr(cli, "CredentialProvider", NoCredentials)
    with pytest.raises(PublishingError) as raised:
        await cli._resume_operation(
            run_id=receipt.run_id,
            config_path=config,
            app_alias="release",
            interactive=False,
            poll_interval=5,
            wait_timeout=5,
        )
    assert raised.value.code == "PACKAGE_CHANGED"
