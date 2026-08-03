from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import storehelper.cli as cli_module
from storehelper.credentials.models import HuaweiServiceAccount
from storehelper.credentials.providers import MemoryKeyring
from storehelper.domain.models import OperationResult, PublishStage
from storehelper.runs.models import RunReceipt, RunState
from storehelper.runs.repository import RunRepository

runner = CliRunner()


def _project(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "storehelper.yaml"
    config.write_text(
        """version: 1
apps:
  wallet:
    package_name: com.example.wallet
    stores:
      huawei:
        app_id: "123"
        credential_profile: default
        language: zh-CN
""",
        encoding="utf-8",
    )
    package = tmp_path / "wallet.apk"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
    return config, package


def _harmony_project(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "storehelper.yaml"
    config.write_text(
        """version: 1
apps:
  wallet:
    package_name: com.example.wallet
    stores:
      harmonyos:
        app_id: "100000002"
        package_name: com.example.wallet.harmony
        credential_profile: default
        language: zh-CN
""",
        encoding="utf-8",
    )
    package = tmp_path / "wallet.app"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("pack.info", b"{}")
        archive.writestr("entry.hap", b"hap")
    return config, package


def test_noninteractive_submit_requires_yes(tmp_path: Path) -> None:
    config, package = _project(tmp_path)

    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--app",
            "wallet",
            "--file",
            str(package),
            "--release-notes",
            "Fixes",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 2
    assert "--yes" in result.stderr


def test_json_publish_stdout_is_parseable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, package = _project(tmp_path)

    async def fake_publish(**kwargs) -> OperationResult:
        assert kwargs["request"].confirmed is True
        return OperationResult.success(stage=PublishStage.SUBMITTED, run_id="run-1")

    monkeypatch.setattr(cli_module, "_publish_operation", fake_publish)
    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--app",
            "wallet",
            "--file",
            str(package),
            "--release-notes",
            "Fixes",
            "--yes",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["stage"] == "submitted"


def test_dry_run_does_not_require_credentials(tmp_path: Path, monkeypatch) -> None:
    config, package = _project(tmp_path)
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")

    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--app",
            "wallet",
            "--file",
            str(package),
            "--dry-run",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["stage"] == "completed"


def test_harmonyos_dry_run_uses_selected_store_without_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, package = _harmony_project(tmp_path)
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")

    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--app",
            "wallet",
            "--store",
            "harmonyos",
            "--file",
            str(package),
            "--dry-run",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["store"] == "harmonyos"
    receipt = RunRepository(tmp_path / "runs").list()[0]
    assert receipt.store.value == "harmonyos"


def test_no_submit_does_not_require_release_notes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, package = _project(tmp_path)

    async def fake_publish(**kwargs) -> OperationResult:
        request = kwargs["request"]
        assert request.submit is False
        assert request.release_notes is None
        return OperationResult.success(stage=PublishStage.PACKAGE_READY, run_id="run-2")

    monkeypatch.setattr(cli_module, "_publish_operation", fake_publish)
    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--app",
            "wallet",
            "--file",
            str(package),
            "--no-submit",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 0
    assert "package_ready" in result.stdout


def test_resume_timeout_uses_exit_6_and_next_action(monkeypatch) -> None:
    async def fake_resume(**kwargs) -> OperationResult:
        return OperationResult.failure(
            stage=PublishStage.TIMED_OUT,
            run_id=kwargs["run_id"],
            message="Still compiling",
            resumable=True,
        )

    monkeypatch.setattr(cli_module, "_resume_operation", fake_resume)
    result = runner.invoke(
        cli_module.app,
        ["resume", "run-1", "--app", "wallet", "--output", "json"],
    )

    assert result.exit_code == 6
    assert json.loads(result.stdout)["next_action"]["command"] == "storehelper resume run-1"


def test_runs_list_show_and_delete(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")
    repo = RunRepository(cli_module.RUNS_ROOT)
    now = datetime.now(UTC)
    receipt = RunReceipt(
        run_id="run-1",
        created_at=now,
        updated_at=now,
        store="huawei",
        state=RunState.PACKAGE_COMPILING,
        app_alias="wallet",
        app_id="123",
        package_name="com.example.wallet",
        package_path="/build/wallet.apk",
        package_sha256="abc",
        logical_name="wallet.apk",
        artifact_id="42",
        language="zh-CN",
        release_notes="Fixes",
    )
    repo.save(receipt)

    listed = runner.invoke(cli_module.app, ["runs", "list", "--output", "json"])
    listed_text = runner.invoke(cli_module.app, ["runs", "list"])
    shown = runner.invoke(cli_module.app, ["runs", "show", "run-1", "--output", "json"])
    shown_text = runner.invoke(cli_module.app, ["runs", "show", "run-1"])
    deleted = runner.invoke(
        cli_module.app,
        ["runs", "delete", "run-1", "--yes", "--output", "json"],
    )

    assert json.loads(listed.stdout)["runs"][0]["run_id"] == "run-1"
    assert "run-1  wallet  package_compiling" in listed_text.stdout
    assert json.loads(shown.stdout)["artifact_id"] == "42"
    assert "Package: wallet.apk" in shown_text.stdout
    assert deleted.exit_code == 0
    assert json.loads(deleted.stdout)["deleted"] is True
    assert repo.list() == []


def test_status_and_credential_verify_commands(monkeypatch) -> None:
    async def fake_status(**kwargs) -> OperationResult:
        return OperationResult.success(
            stage=PublishStage.COMPLETED,
            run_id=None,
            message="Huawei review status: in_review",
        )

    async def fake_verify(**kwargs) -> OperationResult:
        assert kwargs["profile"] == "work"
        return OperationResult.success(
            stage=PublishStage.APP_VERIFIED,
            run_id=None,
            message="Verified",
        )

    monkeypatch.setattr(cli_module, "_status_operation", fake_status)
    monkeypatch.setattr(cli_module, "_verify_credentials_operation", fake_verify)

    status = runner.invoke(cli_module.app, ["status", "--app", "wallet"])
    verified = runner.invoke(
        cli_module.app,
        ["credentials", "verify", "--app", "wallet", "--profile", "work"],
    )

    assert status.exit_code == 0
    assert "in_review" in status.stdout
    assert verified.exit_code == 0
    assert "Verified" in verified.stdout


def test_publish_rejects_unsupported_store_and_conflicting_notes(tmp_path: Path) -> None:
    config, package = _project(tmp_path)
    notes = tmp_path / "notes.md"
    notes.write_text("Fixes", encoding="utf-8")

    unsupported = runner.invoke(
        cli_module.app,
        ["publish", "--file", str(package), "--store", "xiaomi", "--dry-run"],
    )
    conflict = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--file",
            str(package),
            "--release-notes",
            "Fixes",
            "--release-notes-file",
            str(notes),
            "--yes",
            "--config",
            str(config),
        ],
    )

    assert unsupported.exit_code == 2
    assert "huawei" in unsupported.stderr
    assert "harmonyos" in unsupported.stderr
    assert conflict.exit_code == 2
    assert "RELEASE_NOTES_CONFLICT" in conflict.stderr


def test_noninteractive_submit_requires_release_notes_even_with_yes(tmp_path: Path) -> None:
    config, package = _project(tmp_path)

    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--file",
            str(package),
            "--yes",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 2
    assert "RELEASE_NOTES_MISSING" in result.stderr


def test_publish_reads_release_notes_file_and_validates_durations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, package = _project(tmp_path)
    notes = tmp_path / "notes.md"
    notes.write_text("From file", encoding="utf-8")

    async def fake_publish(**kwargs) -> OperationResult:
        assert kwargs["request"].release_notes == "From file"
        return OperationResult.success(stage=PublishStage.SUBMITTED, run_id="run-3")

    monkeypatch.setattr(cli_module, "_publish_operation", fake_publish)
    published = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--file",
            str(package),
            "--release-notes-file",
            str(notes),
            "--yes",
            "--poll-interval",
            "1m",
            "--wait-timeout",
            "1h",
            "--config",
            str(config),
        ],
    )
    invalid = runner.invoke(
        cli_module.app,
        ["publish", "--file", str(package), "--dry-run", "--poll-interval", "bad"],
    )

    assert published.exit_code == 0
    assert invalid.exit_code == 2


def test_publish_result_vendor_failure_uses_exit_5(tmp_path: Path, monkeypatch) -> None:
    config, package = _project(tmp_path)

    async def fake_publish(**kwargs) -> OperationResult:
        return OperationResult.failure(
            stage=PublishStage.FAILED,
            run_id="run-4",
            message="Rejected",
        )

    monkeypatch.setattr(cli_module, "_publish_operation", fake_publish)
    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--file",
            str(package),
            "--release-notes",
            "Fixes",
            "--yes",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 5


def test_empty_runs_and_missing_delete(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")

    listed = runner.invoke(cli_module.app, ["runs", "list"])
    deleted = runner.invoke(cli_module.app, ["runs", "delete", "missing", "--yes"])

    assert listed.stdout.strip() == "No publishing runs."
    assert deleted.exit_code == 0
    assert "already absent" in deleted.stdout


@pytest.mark.asyncio
async def test_real_cli_operation_factories_use_huawei_adapter(
    tmp_path: Path,
    rsa_private_key: str,
    monkeypatch,
) -> None:
    config, package = _project(tmp_path)
    account = HuaweiServiceAccount(
        key_id="key-1",
        sub_account="sub-1",
        private_key=rsa_private_key,
    )
    keyring = MemoryKeyring()
    keyring.set("default", account.to_storage_json())
    monkeypatch.setattr(cli_module, "KEYRING", keyring)
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")
    real_async_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/appid-list"):
            return httpx.Response(
                200,
                json={"ret": {"code": 0}, "appids": [{"appId": "123"}]},
            )
        if path.endswith("/upload-url"):
            return httpx.Response(
                200,
                json={
                    "ret": {"code": 0},
                    "result": {
                        "uploadUrl": "https://upload.example/file",
                        "authCode": "temporary-secret",
                    },
                },
            )
        if request.url.host == "upload.example":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "UploadFileRsp": {
                            "ifSuccess": 1,
                            "fileInfoList": [{"fileDestUrl": "https://destination.example/object"}],
                        }
                    }
                },
            )
        if path.endswith("/app-file-info"):
            return httpx.Response(200, json={"ret": {"code": 0}, "pkgVersion": ["42"]})
        if path.endswith("/package/compile/status"):
            return httpx.Response(
                200,
                json={
                    "ret": {"code": 0},
                    "pkgStateList": [{"pkgId": "42", "successStatus": 0}],
                },
            )
        if path.endswith("/app-info"):
            return httpx.Response(
                200,
                json={"ret": {"code": 0}, "appInfo": {"releaseState": 4}},
            )
        return httpx.Response(200, json={"ret": {"code": 0}})

    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(handler)),
    )
    request = cli_module.PublishRequest(
        app_alias="wallet",
        file=package,
        release_notes="Fixes",
        confirmed=True,
        poll_interval_seconds=5,
        wait_timeout_seconds=5,
    )

    published = await cli_module._publish_operation(
        request=request,
        config_path=config,
        app_alias="wallet",
        interactive=False,
    )
    status = await cli_module._status_operation(
        config_path=config,
        app_alias="wallet",
        store=cli_module.StoreName.HUAWEI,
        interactive=False,
    )
    verified = await cli_module._verify_credentials_operation(
        config_path=config,
        app_alias="wallet",
        profile=None,
        store=cli_module.StoreName.HUAWEI,
        interactive=False,
    )

    assert published.stage is PublishStage.SUBMITTED
    assert status.message.endswith("in_review")
    assert verified.stage is PublishStage.APP_VERIFIED


@pytest.mark.asyncio
async def test_real_cli_operation_factory_uses_harmonyos_adapter(
    tmp_path: Path,
    rsa_private_key: str,
    monkeypatch,
) -> None:
    config, package = _harmony_project(tmp_path)
    account = HuaweiServiceAccount(
        key_id="key-1",
        sub_account="sub-1",
        private_key=rsa_private_key,
    )
    keyring = MemoryKeyring()
    keyring.set("default", account.to_storage_json())
    monkeypatch.setattr(cli_module, "KEYRING", keyring)
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")
    real_async_client = httpx.AsyncClient
    calls: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        if path.endswith("/appid-list"):
            return httpx.Response(
                200,
                json={"ret": {"code": 0}, "appids": [{"value": "100000002"}]},
            )
        if path.endswith("/upload-url/for-obs"):
            return httpx.Response(
                200,
                json={
                    "ret": {"code": 0},
                    "urlInfo": {
                        "objectId": "private-object-id",
                        "url": "https://obs.example/wallet.app",
                        "method": "PUT",
                        "headers": {
                            "Authorization": "AWS4 temporary-signature",
                            "Content-Length": request.url.params["contentLength"],
                        },
                    },
                },
            )
        if request.url.host == "obs.example":
            await request.aread()
            return httpx.Response(200)
        if path.endswith("/api/publish/v3/app-package-info"):
            return httpx.Response(
                200,
                json={"ret": {"code": 0}, "packageId": "package-42"},
            )
        if path.endswith("/api/publish/v2/app-package-info"):
            return httpx.Response(
                200,
                json={"ret": {"code": 0}, "packageInfo": {"parseStatus": "ready"}},
            )
        if path.endswith("/app-info"):
            return httpx.Response(
                200,
                json={"ret": {"code": 0}, "appInfo": {"releaseState": 4}},
            )
        return httpx.Response(200, json={"ret": {"code": 0}})

    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(handler)),
    )
    request = cli_module.PublishRequest(
        store=cli_module.StoreName.HARMONYOS,
        app_alias="wallet",
        file=package,
        release_notes="修复已知问题",
        confirmed=True,
        poll_interval_seconds=5,
        wait_timeout_seconds=5,
    )

    published = await cli_module._publish_operation(
        request=request,
        config_path=config,
        app_alias="wallet",
        interactive=False,
    )
    status = await cli_module._status_operation(
        config_path=config,
        app_alias="wallet",
        store=cli_module.StoreName.HARMONYOS,
        interactive=False,
    )
    verified = await cli_module._verify_credentials_operation(
        config_path=config,
        app_alias="wallet",
        profile=None,
        store=cli_module.StoreName.HARMONYOS,
        interactive=False,
    )

    assert published.store is cli_module.StoreName.HARMONYOS
    assert published.stage is PublishStage.SUBMITTED
    assert status.store is cli_module.StoreName.HARMONYOS
    assert status.message.endswith("in_review")
    assert verified.store is cli_module.StoreName.HARMONYOS
    assert ("PUT", "/api/publish/v3/app-package-info") in calls


def test_publish_help_lists_registered_store_choices() -> None:
    result = runner.invoke(cli_module.app, ["publish", "--help"])
    collapsed = "".join(result.stdout.split())

    assert result.exit_code == 0
    assert "huawei" in collapsed
    assert "harmonyos" in collapsed
    # Rich wraps the final enum value as ``app`` / ``le`` in its fixed-width option column.
    assert "harmonyos|app" in result.stdout
    assert "le>" in result.stdout


@pytest.mark.asyncio
async def test_dry_run_adapter_fails_fast_if_a_network_method_is_called() -> None:
    adapter = cli_module._NoNetworkAdapter()
    target = cli_module.StoreTarget(
        store=cli_module.StoreName.HUAWEI,
        label="Huawei AppGallery (Android)",
        app_id="1",
        package_name="com.example.app",
        credential_profile="default",
        language="zh-CN",
    )

    with pytest.raises(AssertionError):
        await adapter.verify(target=target)
    with pytest.raises(AssertionError):
        await adapter.submit(target=target, artifact_id="42")
    with pytest.raises(AssertionError):
        await adapter.review_status(target=target)
