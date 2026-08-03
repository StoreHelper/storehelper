"""Stable, secret-safe vivo publishing errors."""

from __future__ import annotations

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.errors import StoreVendorError


class VivoVendorError(StoreVendorError):
    def __init__(
        self,
        code: str,
        message: str,
        exit_code: ExitCode,
        *,
        vendor_code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(code, message, exit_code, vendor_code=vendor_code)
        self.status_code = status_code


_VENDOR_ERRORS: dict[str, tuple[str, str, ExitCode]] = {
    "B0302": (
        "VIVO_UPDATE_CONFLICT",
        "The vivo application already has an update in progress.",
        ExitCode.VENDOR_REJECTION,
    ),
    "20008": (
        "VIVO_REQUEST_INVALID",
        "vivo rejected a required application update value.",
        ExitCode.VENDOR_REJECTION,
    ),
    "AUTH": (
        "VIVO_AUTHENTICATION_FAILED",
        "vivo rejected the publishing credentials or signature.",
        ExitCode.AUTHENTICATION,
    ),
}


def _vendor_code(code: str | None, sub_code: str | None) -> str | None:
    values: list[str] = []
    if code:
        values.append(code)
    if sub_code:
        values.append(sub_code)
    return "/".join(values) or None


def parse_vivo_error(
    *,
    code: str | None,
    sub_code: str | None,
    status_code: int,
    vendor_message: object = None,
) -> VivoVendorError:
    """Map allowlisted codes without copying vendor-controlled response text."""

    del vendor_message
    normalized_code = code.upper() if isinstance(code, str) else None
    normalized_sub = sub_code.upper() if isinstance(sub_code, str) else None
    known = _VENDOR_ERRORS.get(normalized_sub or "") or _VENDOR_ERRORS.get(normalized_code or "")
    vendor_code = _vendor_code(code, sub_code)
    if known is not None:
        stable_code, message, exit_code = known
        return VivoVendorError(
            stable_code,
            message,
            exit_code,
            vendor_code=vendor_code,
            status_code=status_code,
        )
    if status_code in {401, 403}:
        return VivoVendorError(
            "VIVO_AUTHENTICATION_FAILED",
            "vivo rejected the publishing credentials.",
            ExitCode.AUTHENTICATION,
            vendor_code=vendor_code,
            status_code=status_code,
        )
    if status_code == 429 or status_code >= 500:
        return VivoVendorError(
            "VIVO_SERVICE_UNAVAILABLE",
            f"vivo publishing is temporarily unavailable (HTTP {status_code}).",
            ExitCode.NETWORK,
            vendor_code=vendor_code,
            status_code=status_code,
        )
    return VivoVendorError(
        "VIVO_API_REJECTED",
        f"vivo rejected the publishing request (HTTP {status_code}).",
        ExitCode.VENDOR_REJECTION,
        vendor_code=vendor_code,
        status_code=status_code,
    )
