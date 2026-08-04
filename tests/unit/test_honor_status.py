from __future__ import annotations

import pytest

from storehelper.stores.honor.adapter import parse_review_status
from storehelper.stores.models import ReviewStatus


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, ReviewStatus.IN_REVIEW),
        (1, ReviewStatus.APPROVED),
        (2, ReviewStatus.REJECTED),
        (3, ReviewStatus.UNKNOWN),
        (4, ReviewStatus.PENDING_REVIEW),
        (5, ReviewStatus.UNKNOWN),
        (None, ReviewStatus.UNKNOWN),
    ],
)
def test_honor_audit_result_mapping_is_explicit(raw: object, expected: ReviewStatus) -> None:
    assert parse_review_status(raw) is expected
