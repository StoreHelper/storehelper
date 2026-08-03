from __future__ import annotations

from datetime import UTC, datetime

import pytest

from storehelper.stores.apple.errors import AppleVendorError
from storehelper.stores.apple.models import validate_upload_plan

NOW = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)


def _operation(
    *,
    offset: int,
    length: int,
    part: int,
    url: str = "https://uploads.example/part",
    method: str = "PUT",
    expiration: str = "2026-08-03T09:00:00Z",
    entity_tag: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "method": method,
        "url": url,
        "offset": offset,
        "length": length,
        "partNumber": part,
        "expiration": expiration,
        "requestHeaders": [
            {"name": "Content-Type", "value": "application/octet-stream"},
            {"name": "x-upload-token", "value": f"secret-{part}"},
        ],
    }
    if entity_tag is not None:
        value["entityTag"] = entity_tag
    return value


def test_sorts_complete_plan_and_marks_delivered_parts_secret() -> None:
    operations = validate_upload_plan(
        [
            _operation(offset=3, length=2, part=2),
            _operation(offset=0, length=3, part=1, entity_tag="private-etag"),
        ],
        5,
        NOW,
    )

    assert [operation.offset for operation in operations] == [0, 3]
    assert operations[0].delivered is True
    assert operations[1].delivered is False
    assert "private-etag" not in repr(operations)
    assert "secret-1" not in repr(operations)


@pytest.mark.parametrize(
    "operations",
    [
        [_operation(offset=1, length=4, part=1)],
        [_operation(offset=0, length=2, part=1), _operation(offset=3, length=2, part=2)],
        [_operation(offset=0, length=4, part=1), _operation(offset=3, length=2, part=2)],
        [_operation(offset=0, length=3, part=1), _operation(offset=3, length=2, part=1)],
        [_operation(offset=0, length=6, part=1)],
    ],
)
def test_rejects_gap_overlap_duplicate_parts_and_overflow(
    operations: list[dict[str, object]],
) -> None:
    with pytest.raises(AppleVendorError) as raised:
        validate_upload_plan(operations, 5, NOW)

    assert raised.value.code == "APPLE_UPLOAD_PLAN_INVALID"


@pytest.mark.parametrize(
    "operation",
    [
        _operation(offset=0, length=5, part=1, url="http://uploads.example/part"),
        _operation(offset=0, length=5, part=1, url="https://user:pass@uploads.example/part"),
        _operation(offset=0, length=5, part=1, method="POST"),
        _operation(offset=0, length=5, part=1, expiration="2026-08-03T07:59:59Z"),
        {**_operation(offset=0, length=5, part=1), "requestHeaders": []},
        {
            **_operation(offset=0, length=5, part=1),
            "requestHeaders": [{"name": "bad header", "value": "secret"}],
        },
    ],
)
def test_rejects_unsafe_or_expired_operations(operation: dict[str, object]) -> None:
    with pytest.raises(AppleVendorError) as raised:
        validate_upload_plan([operation], 5, NOW)

    assert raised.value.code == "APPLE_UPLOAD_PLAN_INVALID"
    assert "secret" not in str(raised.value)
