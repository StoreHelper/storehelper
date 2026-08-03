from __future__ import annotations

import pytest

from storehelper.stores.huawei.adapter import (
    CompileState,
    parse_compile_status,
)
from storehelper.stores.huawei.errors import HuaweiVendorError
from storehelper.stores.models import ProcessingState, ProcessingStatus


@pytest.mark.parametrize(
    ("success_status", "expected"),
    [
        (0, CompileState.READY),
        (1, CompileState.PROCESSING),
        (2, CompileState.FAILED),
    ],
)
def test_maps_documented_compile_status(
    success_status: int,
    expected: CompileState,
) -> None:
    payload = {
        "ret": {"code": 0},
        "pkgStateList": [{"pkgId": "42", "successStatus": success_status}],
    }

    result = parse_compile_status(payload, "42")

    assert isinstance(result, ProcessingStatus)
    assert result.state is expected
    assert result.state is ProcessingState(expected.value)


def test_missing_package_record_is_processing() -> None:
    result = parse_compile_status({"ret": {"code": 0}, "pkgStateList": []}, "42")

    assert result.state is CompileState.PROCESSING


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        (None, CompileState.PROCESSING),
        (0, CompileState.PROCESSING),
        (1, CompileState.PROCESSING),
        (2, CompileState.READY),
        (3, CompileState.FAILED),
    ],
)
def test_maps_legacy_aab_compile_status(legacy: int | None, expected: CompileState) -> None:
    item: dict[str, object] = {"pkgId": "42"}
    if legacy is not None:
        item["aabCompileStatus"] = legacy

    result = parse_compile_status({"pkgStateList": [item]}, "42")

    assert result.state is expected


def test_compile_failure_preserves_only_bounded_safe_reason() -> None:
    payload = {
        "pkgStateList": [
            {
                "pkgId": "42",
                "successStatus": 2,
                "failReason": "signature mismatch access_token=super-secret" * 30,
            }
        ]
    }

    result = parse_compile_status(payload, "42")

    assert result.reason is not None
    assert len(result.reason) <= 500
    assert "super-secret" not in result.reason


def test_unknown_success_status_is_rejected() -> None:
    with pytest.raises(HuaweiVendorError, match="unknown compile status"):
        parse_compile_status(
            {"pkgStateList": [{"pkgId": "42", "successStatus": 99}]},
            "42",
        )
