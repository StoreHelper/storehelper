from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

import storehelper.cli as cli_module
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
        state=RunState.PACKAGE_COMPILING,
        app_alias="wallet",
        app_id="123",
        package_name="com.example.wallet",
        package_path="/build/wallet.apk",
        package_sha256="abc",
        logical_name="wallet.apk",
        pkg_version="42",
        language="zh-CN",
        release_notes="Fixes",
    )
    repo.save(receipt)

    listed = runner.invoke(cli_module.app, ["runs", "list", "--output", "json"])
    shown = runner.invoke(cli_module.app, ["runs", "show", "run-1", "--output", "json"])
    deleted = runner.invoke(cli_module.app, ["runs", "delete", "run-1", "--yes"])

    assert json.loads(listed.stdout)["runs"][0]["run_id"] == "run-1"
    assert json.loads(shown.stdout)["pkg_version"] == "42"
    assert deleted.exit_code == 0
    assert repo.list() == []
