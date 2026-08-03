from __future__ import annotations

import pytest

from storehelper.stores.harmonyos.adapter import parse_processing_status
from storehelper.stores.huawei.errors import HuaweiVendorError
from storehelper.stores.models import ProcessingState


@pytest.mark.parametrize("package_info", [None, {}, []])
def test_missing_or_empty_package_info_is_processing(package_info: object) -> None:
    payload: dict[str, object] = {"ret": {"code": 0}}
    if package_info is not None:
        payload["packageInfo"] = package_info

    status = parse_processing_status(payload)

    assert status.state is ProcessingState.PROCESSING


def test_populated_package_info_without_state_is_ready() -> None:
    status = parse_processing_status({"ret": {"code": 0}, "packageInfo": {"versionName": "1.2.3"}})

    assert status.state is ProcessingState.READY


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PROCESSING", ProcessingState.PROCESSING),
        ("pending", ProcessingState.PROCESSING),
        ("READY", ProcessingState.READY),
        ("success", ProcessingState.READY),
        ("FAILED", ProcessingState.FAILED),
        ("error", ProcessingState.FAILED),
    ],
)
def test_maps_explicit_text_processing_states(raw: str, expected: ProcessingState) -> None:
    status = parse_processing_status(
        {"packageInfo": {"status": raw, "failReason": "invalid package"}}
    )

    assert status.state is expected
    if expected is ProcessingState.FAILED:
        assert status.reason == "invalid package"


def test_failure_reason_is_bounded_and_redacted() -> None:
    status = parse_processing_status(
        {
            "packageInfo": {
                "parseStatus": "failed",
                "failReason": "objectId=private-object " * 100,
            }
        }
    )

    assert status.reason is not None
    assert len(status.reason) <= 500
    assert "private-object" not in status.reason


def test_unknown_explicit_state_is_rejected() -> None:
    with pytest.raises(HuaweiVendorError, match="unknown package processing status"):
        parse_processing_status({"packageInfo": {"compileStatus": "MYSTERY"}})
