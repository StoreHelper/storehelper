"""Sanitized App Store Connect JSON:API errors."""

from __future__ import annotations

import re
from collections.abc import Mapping

from storehelper.domain.errors import redact
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.errors import StoreVendorError


class AppleVendorError(StoreVendorError):
    """A safe App Store Connect failure with stable StoreHelper semantics."""

    def __init__(
        self,
        code: str,
        message: str,
        exit_code: ExitCode,
        *,
        resumable: bool | None = None,
        vendor_code: str | None = None,
    ) -> None:
        super().__init__(
            code,
            message,
            exit_code,
            resumable=exit_code is ExitCode.NETWORK if resumable is None else resumable,
            vendor_code=vendor_code,
        )


_VENDOR_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")


def _clean(value: object) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    cleaned = redact(str(value)).replace("\r", " ").replace("\n", " ").strip()
    return cleaned[:300] or None


def parse_apple_errors(payload: object, *, status_code: int) -> AppleVendorError:
    exit_code = ExitCode.VENDOR_REJECTION
    code = "APPLE_API_REJECTED"
    if status_code in (401, 403):
        exit_code = ExitCode.AUTHENTICATION
        code = "APPLE_AUTHORIZATION_FAILED"
    elif status_code == 429 or status_code >= 500:
        exit_code = ExitCode.NETWORK
        code = "APPLE_NETWORK_ERROR"

    values: list[str] = []
    vendor_code: str | None = None
    if isinstance(payload, Mapping):
        errors = payload.get("errors")
        if isinstance(errors, list):
            for raw in errors[:3]:
                if not isinstance(raw, Mapping):
                    continue
                raw_code = raw.get("code")
                current_code = _clean(raw_code)
                if vendor_code is None:
                    candidate = str(raw_code) if isinstance(raw_code, (str, int)) else ""
                    if _VENDOR_CODE.fullmatch(candidate):
                        vendor_code = candidate
                fields = [
                    _clean(raw.get("status")),
                    current_code,
                    _clean(raw.get("title")),
                    _clean(raw.get("detail")),
                ]
                message = ": ".join(value for value in fields if value)
                if message:
                    values.append(message)
    detail = "; ".join(values)[:800]
    message = f"App Store Connect returned HTTP {status_code}."
    if detail:
        message = f"{message} {detail}"
    return AppleVendorError(
        code,
        message,
        exit_code,
        vendor_code=vendor_code,
    )
