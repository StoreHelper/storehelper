from __future__ import annotations

import pytest

from storehelper.stores.apple.adapter import parse_review_status
from storehelper.stores.apple.errors import AppleVendorError
from storehelper.stores.models import ReviewStatus


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("READY_FOR_REVIEW", ReviewStatus.PENDING_REVIEW),
        ("WAITING_FOR_REVIEW", ReviewStatus.PENDING_REVIEW),
        ("IN_REVIEW", ReviewStatus.IN_REVIEW),
        ("REJECTED", ReviewStatus.REJECTED),
        ("METADATA_REJECTED", ReviewStatus.REJECTED),
        ("ACCEPTED", ReviewStatus.APPROVED),
        ("PENDING_APPLE_RELEASE", ReviewStatus.APPROVED),
        ("READY_FOR_SALE", ReviewStatus.APPROVED),
        ("DEVELOPER_REMOVED_FROM_SALE", ReviewStatus.SUSPENDED),
        ("UNKNOWN_FUTURE_STATE", ReviewStatus.UNKNOWN),
    ],
)
def test_maps_app_store_version_states(state: str, expected: ReviewStatus) -> None:
    payload = {
        "data": {
            "type": "appStoreVersions",
            "id": "version-456",
            "attributes": {"appStoreState": state},
        }
    }

    assert parse_review_status(payload) is expected


def test_rejects_malformed_review_status_payload() -> None:
    with pytest.raises(AppleVendorError) as raised:
        parse_review_status({"data": []})

    assert raised.value.code == "APPLE_REVIEW_STATUS_INVALID"
