from __future__ import annotations

import pytest

from storehelper.stores.apple.adapter import parse_build_upload_status
from storehelper.stores.apple.errors import AppleVendorError
from storehelper.stores.models import ProcessingState


def _payload(
    state: str,
    *,
    build_id: str | None = None,
    errors: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    relationships: dict[str, object] = {"build": {"data": None}}
    if build_id is not None:
        relationships = {"build": {"data": {"type": "builds", "id": build_id}}}
    return {
        "data": {
            "type": "buildUploads",
            "id": "upload-456",
            "attributes": {"state": {"state": state, "errors": errors or []}},
            "relationships": relationships,
        }
    }


@pytest.mark.parametrize("state", ["AWAITING_UPLOAD", "PROCESSING"])
def test_maps_unfinished_build_upload_states(state: str) -> None:
    status = parse_build_upload_status(_payload(state))

    assert status.state is ProcessingState.PROCESSING
    assert status.artifact_id is None


def test_complete_returns_final_build_resource_id() -> None:
    status = parse_build_upload_status(_payload("COMPLETE", build_id="build-999"))

    assert status.state is ProcessingState.READY
    assert status.artifact_id == "build-999"


def test_complete_without_build_relationship_remains_processing() -> None:
    status = parse_build_upload_status(_payload("COMPLETE"))

    assert status.state is ProcessingState.PROCESSING


def test_failed_state_uses_only_sanitized_state_details() -> None:
    status = parse_build_upload_status(
        _payload(
            "FAILED",
            errors=[
                {
                    "code": "ITMS-90000",
                    "description": "private_key=never-print invalid bundle",
                    "meta": {"token": "also-secret"},
                }
            ],
        )
    )

    assert status.state is ProcessingState.FAILED
    assert status.reason is not None
    assert "ITMS-90000" in status.reason
    assert "never-print" not in status.reason
    assert "also-secret" not in status.reason


@pytest.mark.parametrize("payload", [_payload("UNKNOWN"), {"data": []}])
def test_rejects_unknown_or_malformed_processing_payload(payload: dict[str, object]) -> None:
    with pytest.raises(AppleVendorError) as raised:
        parse_build_upload_status(payload)

    assert raised.value.code == "APPLE_BUILD_UPLOAD_STATE_INVALID"
