from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from storehelper.runs.models import RunReceipt, RunState
from storehelper.runs.repository import RunRepository, StateError


def sample_receipt(
    *,
    app_id: str = "123",
    sha256: str = "abc",
    state: RunState = RunState.PACKAGE_COMPILING,
) -> RunReceipt:
    now = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)
    return RunReceipt(
        run_id="20260730T100000Z-a1b2c3d4",
        created_at=now,
        updated_at=now,
        state=state,
        app_alias="demo",
        app_id=app_id,
        package_name="com.example.app",
        package_path="/build/release.apk",
        package_sha256=sha256,
        logical_name="release.apk",
        pkg_version="42",
        language="zh-CN",
        release_notes="Fixes",
        submit=True,
    )


def test_receipt_round_trip_and_duplicate_lookup(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path)
    receipt = sample_receipt()

    repo.save(receipt)

    assert repo.get(receipt.run_id) == receipt
    assert repo.find_resumable("huawei", "123", "abc") == receipt
    assert repo.list() == [receipt]
    mode = os.stat(tmp_path / f"{receipt.run_id}.json").st_mode & 0o777
    assert mode & 0o077 == 0


def test_receipt_rejects_secret_fields() -> None:
    payload = sample_receipt().model_dump(mode="json")
    payload["access_token"] = "secret"

    with pytest.raises(ValidationError):
        RunReceipt.model_validate(payload)


def test_create_generates_sortable_unique_run_ids(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path)

    first = repo.create(
        app_alias="demo",
        app_id="123",
        package_name="com.example.app",
        package_path="/build/release.apk",
        package_sha256="abc",
        logical_name="release.apk",
        language="zh-CN",
        release_notes="Fixes",
        submit=True,
    )
    second = repo.create(
        app_alias="demo",
        app_id="123",
        package_name="com.example.app",
        package_path="/build/release.apk",
        package_sha256="def",
        logical_name="release.apk",
        language="zh-CN",
        release_notes=None,
        submit=False,
    )

    assert first.run_id != second.run_id
    assert first.run_id[:16].endswith("Z")
    assert repo.get(first.run_id) == first


def test_terminal_receipts_are_not_resumable(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path)
    repo.save(sample_receipt(state=RunState.COMPLETED))

    assert repo.find_resumable("huawei", "123", "abc") is None


def test_malformed_receipt_is_reported_not_deleted(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"private_key":"secret"}', encoding="utf-8")
    repo = RunRepository(tmp_path)

    with pytest.raises(StateError) as raised:
        repo.get("broken")

    assert raised.value.code == "STATE_CORRUPT"
    assert path.exists()
    assert "secret" not in str(raised.value)


def test_delete_removes_only_named_receipt(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path)
    receipt = sample_receipt()
    repo.save(receipt)

    assert repo.delete(receipt.run_id) is True
    assert repo.delete(receipt.run_id) is False


def test_save_replaces_complete_json_not_partial_content(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path)
    receipt = sample_receipt()
    repo.save(receipt)
    updated = receipt.model_copy(
        update={"state": RunState.PACKAGE_READY, "updated_at": datetime.now(UTC)}
    )

    repo.save(updated)

    payload = json.loads((tmp_path / f"{receipt.run_id}.json").read_text())
    assert payload["state"] == "package_ready"
    assert not list(tmp_path.glob("*.tmp"))
