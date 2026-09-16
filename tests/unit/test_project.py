from __future__ import annotations

import json
from pathlib import Path

import pytest

from storehelper.project import ProjectError, project_path, project_root
from storehelper.runs.repository import RunRepository, StateError
from storehelper.stores.models import StoreName


def test_unset_scope_preserves_existing_path_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STOREHELPER_PROJECT_ROOT", raising=False)
    relative = Path("../anywhere.txt")
    assert project_root() is None
    assert project_path(relative, "test") == relative


def test_relative_paths_resolve_against_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STOREHELPER_PROJECT_ROOT", str(tmp_path))
    assert project_path(Path("build/app.apk"), "artifact") == tmp_path / "build" / "app.apk"


def test_file_and_loop_are_invalid_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = tmp_path / "file"
    candidate.write_text("data", encoding="utf-8")
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    for path in (candidate, loop):
        monkeypatch.setenv("STOREHELPER_PROJECT_ROOT", str(path))
        with pytest.raises(ProjectError, match="absolute path"):
            project_root()


def test_scoped_repository_creates_private_local_receipt_and_preserves_global(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    global_root = tmp_path / "global"
    repo = RunRepository(global_root, project_root=root)
    receipt = repo.create(
        store=StoreName.HUAWEI,
        app_alias="demo",
        app_id="123",
        package_name="com.example.demo",
        package_path=str(root / "app.apk"),
        package_sha256="abc",
        logical_name="app.apk",
        language="zh-CN",
        release_notes=None,
        submit=False,
    )
    path = root / ".storehelper" / "runs" / f"{receipt.run_id}.json"
    assert repo.get(receipt.run_id) == receipt
    assert path.is_file()
    assert path.stat().st_mode & 0o077 == 0
    assert path.parent.stat().st_mode & 0o077 == 0
    assert not global_root.exists()
    assert repo.delete(receipt.run_id)
    assert not repo.delete(receipt.run_id)


def test_scoped_receipt_rejects_relative_package_and_mismatched_id(tmp_path: Path) -> None:
    repo = RunRepository(project_root=tmp_path)
    directory = tmp_path / ".storehelper" / "runs"
    legacy = RunRepository(directory)
    receipt = legacy.create(
        store=StoreName.HUAWEI,
        app_alias="demo",
        app_id="123",
        package_name="com.example.demo",
        package_path="app.apk",
        package_sha256="abc",
        logical_name="app.apk",
        language="zh-CN",
        release_notes=None,
        submit=False,
    )
    with pytest.raises(StateError, match="absolute artifact"):
        repo.get(receipt.run_id)
    payload = receipt.model_dump(mode="json")
    payload["run_id"] = "different-id"
    (directory / f"{receipt.run_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(StateError, match="unreadable or invalid"):
        repo.get(receipt.run_id)
