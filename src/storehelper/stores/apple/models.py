"""Validated in-memory values for native Apple delivery operations."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.apple.errors import AppleVendorError

_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class AppleUploadOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: SecretStr
    method: Literal["PUT"]
    offset: int = Field(ge=0)
    length: int = Field(gt=0)
    part_number: int = Field(gt=0)
    headers: dict[str, SecretStr]
    expiration: datetime
    entity_tag: SecretStr | None = None

    @property
    def delivered(self) -> bool:
        return self.entity_tag is not None


class AppleUploadReservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    upload_id: str = Field(min_length=1)
    file_id: str = Field(min_length=1)
    operations: tuple[AppleUploadOperation, ...] = Field(min_length=1)


def _invalid() -> AppleVendorError:
    return AppleVendorError(
        "APPLE_UPLOAD_PLAN_INVALID",
        "App Store Connect returned an invalid delivery upload plan.",
        ExitCode.VENDOR_REJECTION,
    )


def _integer(value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _invalid()
    return value


def _expiration(value: object) -> datetime:
    if not isinstance(value, str):
        raise _invalid()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _invalid() from None
    if parsed.tzinfo is None:
        raise _invalid()
    return parsed.astimezone(UTC)


def _headers(value: object) -> dict[str, SecretStr]:
    if not isinstance(value, list) or not value:
        raise _invalid()
    headers: dict[str, SecretStr] = {}
    normalized_names: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise _invalid()
        name = item.get("name")
        header_value = item.get("value")
        if (
            not isinstance(name, str)
            or not _HEADER_NAME.fullmatch(name)
            or not isinstance(header_value, str)
            or not header_value
            or "\r" in header_value
            or "\n" in header_value
        ):
            raise _invalid()
        normalized = name.lower()
        if normalized in normalized_names:
            raise _invalid()
        normalized_names.add(normalized)
        headers[name] = SecretStr(header_value)
    return headers


def validate_upload_plan(
    operations: Sequence[Mapping[str, object]],
    file_size: int,
    now: datetime,
) -> tuple[AppleUploadOperation, ...]:
    """Validate a complete, gap-free set of signed Apple delivery ranges."""

    if isinstance(file_size, bool) or file_size < 1 or not operations:
        raise _invalid()
    current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    parsed: list[AppleUploadOperation] = []
    parts: set[int] = set()
    for raw in operations:
        method = raw.get("method")
        url = raw.get("url")
        if method != "PUT" or not isinstance(url, str):
            raise _invalid()
        location = urlsplit(url)
        if (
            location.scheme.lower() != "https"
            or not location.hostname
            or location.username is not None
            or location.password is not None
        ):
            raise _invalid()
        offset = _integer(raw.get("offset"), minimum=0)
        length = _integer(raw.get("length"), minimum=1)
        part_number = _integer(raw.get("partNumber"), minimum=1)
        if part_number in parts:
            raise _invalid()
        parts.add(part_number)
        expiration = _expiration(raw.get("expiration"))
        if expiration <= current.astimezone(UTC):
            raise _invalid()
        entity_tag = raw.get("entityTag")
        if entity_tag is not None and (not isinstance(entity_tag, str) or not entity_tag):
            raise _invalid()
        parsed.append(
            AppleUploadOperation(
                url=SecretStr(url),
                method="PUT",
                offset=offset,
                length=length,
                part_number=part_number,
                headers=_headers(raw.get("requestHeaders")),
                expiration=expiration,
                entity_tag=SecretStr(entity_tag) if isinstance(entity_tag, str) else None,
            )
        )
    ordered = sorted(parsed, key=lambda operation: operation.offset)
    next_offset = 0
    for operation in ordered:
        if operation.offset != next_offset or operation.offset + operation.length > file_size:
            raise _invalid()
        next_offset += operation.length
    if next_offset != file_size:
        raise _invalid()
    return tuple(ordered)
