from __future__ import annotations

import pytest

from storehelper.stores.google_play.adapter import parse_review_status
from storehelper.stores.google_play.errors import GoogleVendorError
from storehelper.stores.models import ReviewStatus


@pytest.mark.parametrize(
    ("lifecycle", "expected"),
    [
        ("RELEASE_LIFECYCLE_STATE_DRAFT", ReviewStatus.PENDING_REVIEW),
        ("RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW", ReviewStatus.PENDING_REVIEW),
        ("RELEASE_LIFECYCLE_STATE_IN_REVIEW", ReviewStatus.IN_REVIEW),
        ("RELEASE_LIFECYCLE_STATE_APPROVED_NOT_PUBLISHED", ReviewStatus.APPROVED),
        ("RELEASE_LIFECYCLE_STATE_NOT_APPROVED", ReviewStatus.REJECTED),
        ("RELEASE_LIFECYCLE_STATE_PUBLISHED", ReviewStatus.APPROVED),
        ("RELEASE_LIFECYCLE_STATE_UNSPECIFIED", ReviewStatus.UNKNOWN),
        ("FUTURE_STATE", ReviewStatus.UNKNOWN),
    ],
)
def test_maps_google_release_lifecycle_states(
    lifecycle: str,
    expected: ReviewStatus,
) -> None:
    payload = {"releases": [{"releaseLifecycleState": lifecycle}]}

    assert parse_review_status(payload) is expected


def test_multiple_release_status_prefers_the_most_actionable_state() -> None:
    payload = {
        "releases": [
            {"releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_PUBLISHED"},
            {"releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_DRAFT"},
            {"releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_IN_REVIEW"},
            {"releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_NOT_APPROVED"},
        ]
    }

    assert parse_review_status(payload) is ReviewStatus.REJECTED


@pytest.mark.parametrize("payload", [{}, {"releases": []}])
def test_empty_release_access_is_a_valid_unknown_status(payload: dict[str, object]) -> None:
    assert parse_review_status(payload) is ReviewStatus.UNKNOWN


@pytest.mark.parametrize(
    "payload",
    [
        {"releases": "wrong"},
        {"releases": ["wrong"]},
        {"releases": [{"releaseLifecycleState": 1}]},
        {"releases": [{}]},
    ],
)
def test_malformed_lifecycle_response_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(GoogleVendorError) as raised:
        parse_review_status(payload)

    assert raised.value.code == "GOOGLE_RESPONSE_INVALID"
