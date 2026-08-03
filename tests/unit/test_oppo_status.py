from __future__ import annotations

import pytest

from storehelper.stores.models import ReviewStatus
from storehelper.stores.oppo.adapter import map_oppo_review_status


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, ReviewStatus.PENDING_REVIEW),
        ("0", ReviewStatus.PENDING_REVIEW),
        (1, ReviewStatus.IN_REVIEW),
        (4, ReviewStatus.IN_REVIEW),
        (2, ReviewStatus.APPROVED),
        (6, ReviewStatus.APPROVED),
        (7, ReviewStatus.APPROVED),
        (111, ReviewStatus.APPROVED),
        (3, ReviewStatus.REJECTED),
        (5, ReviewStatus.REJECTED),
        (444, ReviewStatus.REJECTED),
        (222, ReviewStatus.SUSPENDED),
        (999, ReviewStatus.UNKNOWN),
        ("unexpected", ReviewStatus.UNKNOWN),
        (None, ReviewStatus.UNKNOWN),
        (True, ReviewStatus.UNKNOWN),
    ],
)
def test_oppo_audit_status_mapping(raw: object, expected: ReviewStatus) -> None:
    assert map_oppo_review_status(raw) is expected
