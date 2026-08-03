"""Sanitized Google Play Developer API errors."""

from __future__ import annotations

import re
from collections.abc import Mapping

from storehelper.domain.errors import redact
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.errors import StoreVendorError

_VENDOR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")


class GoogleVendorError(StoreVendorError):
    """A safe Google Play failure with stable StoreHelper semantics."""

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


def _clean_message(value: object) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    cleaned = redact(str(value)).replace("\r", " ").replace("\n", " ").strip()
    return cleaned[:500] or None


def parse_google_error(payload: object, *, status_code: int) -> GoogleVendorError:
    code = "GOOGLE_API_REJECTED"
    exit_code = ExitCode.VENDOR_REJECTION
    if status_code in (401, 403):
        code = "GOOGLE_AUTHORIZATION_FAILED"
        exit_code = ExitCode.AUTHENTICATION
    elif status_code == 404:
        code = "GOOGLE_RESOURCE_NOT_FOUND"
    elif status_code == 429 or status_code >= 500:
        code = "GOOGLE_NETWORK_ERROR"
        exit_code = ExitCode.NETWORK

    vendor_code: str | None = None
    detail: str | None = None
    if isinstance(payload, Mapping):
        raw_error = payload.get("error")
        if isinstance(raw_error, Mapping):
            raw_status = raw_error.get("status")
            if isinstance(raw_status, str) and _VENDOR_CODE.fullmatch(raw_status):
                vendor_code = raw_status
            detail = _clean_message(raw_error.get("message"))

    message = f"Google Play Developer API returned HTTP {status_code}."
    if detail:
        message = f"{message} {detail}"
    return GoogleVendorError(
        code,
        message,
        exit_code,
        vendor_code=vendor_code,
    )
