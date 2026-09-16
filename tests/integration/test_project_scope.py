"""Project-scoped CLI behavior must not fall back to another project's state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import storehelper.cli as cli
from storehelper.runs.models import RunReceipt
from storehelper.runs.repository import RunRepository
from storehelper.stores.models import StoreName

runner = CliRunner()


def _receipt(directory: Path, artifact: Path) -> RunReceipt:
    return RunRepository(directory).create(
        store=StoreName.HUAWEI,
        app_alias="demo",
        app_id="123",
        package_name="com.example.demo",
        package_path=str(artifact),
        package_sha256="abc",
        logical_name="demo.apk",
        language="zh-CN",
        release_notes="Private release notes",
        submit=True,
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setenv("STOREHELPER_PROJECT_ROOT", str(root))
    monkeypatch.setattr(cli, "RUNS_ROOT", tmp_path / "global-runs")
    return root


def test_scoped_list_ignores_global_state_without_creating_local_state(project: Path) -> None:
    assert cli.RUNS_ROOT is not None
    _receipt(cli.RUNS_ROOT, project.parent / "other.apk")
    result = runner.invoke(cli.app, ["runs", "list", "--output", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"runs": []}
    assert not (project / ".storehelper").exists()


@pytest.mark.parametrize("command", ["show", "delete", "resume"])
def test_global_receipts_cannot_be_read_deleted_or_resumed(project: Path, command: str) -> None:
    assert cli.RUNS_ROOT is not None
    receipt = _receipt(cli.RUNS_ROOT, project.parent / "other.apk")
    args = ["resume", receipt.run_id] if command == "resume" else ["runs", command, receipt.run_id]
    if command == "delete":
        args.append("--yes")
    result = runner.invoke(cli.app, [*args, "--output", "json"])
    data = json.loads(result.output)
    if command == "delete":
        assert data == {"ok": True, "deleted": False}
    else:
        assert data["code"] == "STATE_NOT_FOUND"
    assert (cli.RUNS_ROOT / f"{receipt.run_id}.json").is_file()


def test_scoped_list_and_show_read_only_the_selected_project(project: Path) -> None:
    receipt = _receipt(project / ".storehelper" / "runs", project / "demo.apk")
    result = runner.invoke(cli.app, ["runs", "list", "--output", "json"])
    assert result.exit_code == 0, result.output
    assert [r["run_id"] for r in json.loads(result.output)["runs"]] == [receipt.run_id]
    result = runner.invoke(cli.app, ["runs", "show", receipt.run_id, "--output", "json"])
    assert json.loads(result.output)["run_id"] == receipt.run_id


@pytest.mark.parametrize("symlink_part", [".storehelper", ".storehelper/runs"])
def test_state_directory_symlinks_cannot_escape(project: Path, symlink_part: str) -> None:
    outside = project.parent / "outside"
    outside.mkdir()
    link = project / symlink_part
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    result = runner.invoke(cli.app, ["runs", "list", "--output", "json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["code"] == "PROJECT_PATH_OUTSIDE_ROOT"


def test_receipt_symlink_cannot_expose_other_project(project: Path) -> None:
    receipt = _receipt(project.parent / "outside", project.parent / "secret.apk")
    directory = project / ".storehelper" / "runs"
    directory.mkdir(parents=True)
    (directory / f"{receipt.run_id}.json").symlink_to(
        project.parent / "outside" / f"{receipt.run_id}.json"
    )
    result = runner.invoke(cli.app, ["runs", "show", receipt.run_id, "--output", "json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["code"] == "PROJECT_PATH_OUTSIDE_ROOT"
    assert "Private release notes" not in result.output


@pytest.mark.parametrize("command", ["show", "list", "resume"])
def test_copied_receipt_with_external_artifact_is_rejected(project: Path, command: str) -> None:
    receipt = _receipt(project / ".storehelper" / "runs", project.parent / "secret.apk")
    args = ["resume", receipt.run_id] if command == "resume" else ["runs", command]
    if command == "show":
        args.append(receipt.run_id)
    result = runner.invoke(cli.app, [*args, "--output", "json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["code"] == "PROJECT_PATH_OUTSIDE_ROOT"
    assert "Private release notes" not in result.output


@pytest.mark.parametrize("value", ["", "missing-directory", "relative-root"])
def test_invalid_scope_fails_closed(
    project: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("STOREHELPER_PROJECT_ROOT", value)
    result = runner.invoke(cli.app, ["runs", "list", "--output", "json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["code"] == "PROJECT_ROOT_INVALID"


def test_nested_xiaomi_icon_path_cannot_escape(project: Path) -> None:
    icon = project.parent / "secret.png"
    icon.write_bytes(b"private icon")
    (project / "icon.png").symlink_to(icon)
    config = project / "storehelper.yaml"
    config.write_text(
        "version: 1\napps:\n  demo:\n    package_name: com.example.demo\n"
        "    stores:\n      xiaomi:\n        credential_profile: default\n"
        "        app_name: Demo\n        icon: icon.png\n"
        "        privacy_url: https://example.com/privacy\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        cli.app, ["config", "validate", "--config", str(config), "--output", "json"]
    )
    assert result.exit_code != 0
    assert json.loads(result.output)["code"] == "PROJECT_PATH_OUTSIDE_ROOT"


@pytest.mark.parametrize("field", ["--file", "--release-notes-file", "--config"])
def test_direct_publish_paths_are_confined_before_use(project: Path, field: str) -> None:
    artifact = project / "app.apk"
    artifact.write_bytes(b"package")
    outside = project.parent / "private.apk"
    outside.write_bytes(b"private data")
    args = ["publish", "--dry-run", "--output", "json", "--file", str(artifact)]
    if field == "--file":
        args[-1] = str(outside)
    else:
        args.extend([field, str(outside)])
    result = runner.invoke(cli.app, args)
    assert result.exit_code != 0
    assert json.loads(result.output)["code"] == "PROJECT_PATH_OUTSIDE_ROOT"
