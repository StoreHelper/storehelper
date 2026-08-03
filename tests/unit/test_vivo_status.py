from __future__ import annotations

import pytest

from storehelper.stores.models import ReviewStatus
from storehelper.stores.vivo.adapter import map_vivo_review_status


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, ReviewStatus.PENDING_REVIEW),
        ("1", ReviewStatus.PENDING_REVIEW),
        (2, ReviewStatus.IN_REVIEW),
        (3, ReviewStatus.APPROVED),
        (4, ReviewStatus.REJECTED),
        (5, ReviewStatus.APPROVED),
        (6, ReviewStatus.SUSPENDED),
        (999, ReviewStatus.UNKNOWN),
        ("unexpected", ReviewStatus.UNKNOWN),
        (None, ReviewStatus.UNKNOWN),
        (True, ReviewStatus.UNKNOWN),
    ],
)
def test_vivo_status_mapping(raw: object, expected: ReviewStatus) -> None:
    assert map_vivo_review_status(raw) is expected
