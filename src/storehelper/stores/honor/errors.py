"""Stable, secret-safe HONOR publishing errors."""

from __future__ import annotations

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.errors import StoreVendorError


class HonorVendorError(StoreVendorError):
    def __init__(
        self,
        code: str,
        message: str,
        exit_code: ExitCode,
        *,
        vendor_code: str | None = None,
        status_code: int | None = None,
        resumable: bool = False,
    ) -> None:
        super().__init__(
            code,
            message,
            exit_code,
            vendor_code=vendor_code,
            resumable=resumable,
        )
        self.status_code = status_code


def parse_honor_error(
    *,
    vendor_code: object,
    status_code: int,
    vendor_message: object = None,
    resumable: bool = False,
) -> HonorVendorError:
    """Map allowlisted HONOR codes without copying vendor-controlled text."""

    del vendor_message
    code = str(vendor_code) if isinstance(vendor_code, (int, str)) else None
    if code in {str(value) for value in range(10001, 10008)}:
        return HonorVendorError(
            "HONOR_AUTHENTICATION_FAILED",
            "HONOR rejected the access token or application permission.",
            ExitCode.AUTHENTICATION,
            vendor_code=code,
            status_code=status_code,
        )
    known = {
        "20005": (
            "HONOR_APPLICATION_NOT_FOUND",
            "HONOR could not find the configured application.",
        ),
        "20009": (
            "HONOR_RELEASE_NOTES_INVALID",
            "HONOR rejected the localized version notes.",
        ),
    }
    if code in known:
        stable_code, message = known[code]
        return HonorVendorError(
            stable_code,
            message,
            ExitCode.VENDOR_REJECTION,
            vendor_code=code,
            status_code=status_code,
            resumable=resumable,
        )
    if status_code in {401, 403}:
        return HonorVendorError(
            "HONOR_AUTHENTICATION_FAILED",
            "HONOR rejected the publishing credentials.",
            ExitCode.AUTHENTICATION,
            vendor_code=code,
            status_code=status_code,
        )
    if status_code == 429 or status_code >= 500:
        return HonorVendorError(
            "HONOR_SERVICE_UNAVAILABLE",
            f"HONOR publishing is temporarily unavailable (HTTP {status_code}).",
            ExitCode.NETWORK,
            vendor_code=code,
            status_code=status_code,
            resumable=resumable,
        )
    return HonorVendorError(
        "HONOR_API_REJECTED",
        f"HONOR rejected the publishing request (HTTP {status_code}).",
        ExitCode.VENDOR_REJECTION,
        vendor_code=code,
        status_code=status_code,
        resumable=resumable,
    )
