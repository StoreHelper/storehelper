"""Stable, secret-safe OPPO publishing errors."""

from __future__ import annotations

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.errors import StoreVendorError


class OppoVendorError(StoreVendorError):
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


_ERRNO_ERRORS: dict[int, tuple[str, str, ExitCode]] = {
    910000: (
        "OPPO_SERVICE_UNAVAILABLE",
        "OPPO publishing is temporarily unavailable.",
        ExitCode.NETWORK,
    ),
    910001: (
        "OPPO_REQUEST_INVALID",
        "OPPO rejected the publishing request parameters.",
        ExitCode.VENDOR_REJECTION,
    ),
    910002: (
        "OPPO_AUTHENTICATION_FAILED",
        "OPPO rejected the publishing credentials or access token.",
        ExitCode.AUTHENTICATION,
    ),
    910003: (
        "OPPO_APPLICATION_NOT_FOUND",
        "The configured application does not exist under this OPPO developer account.",
        ExitCode.VENDOR_REJECTION,
    ),
    911023: (
        "OPPO_APPLICATION_NOT_FOUND",
        "The configured application does not exist under this OPPO developer account.",
        ExitCode.VENDOR_REJECTION,
    ),
    910004: (
        "OPPO_VERSION_NOT_FOUND",
        "The requested OPPO application version does not exist.",
        ExitCode.VENDOR_REJECTION,
    ),
    910005: (
        "OPPO_UPLOAD_REJECTED",
        "OPPO rejected the uploaded package.",
        ExitCode.VENDOR_REJECTION,
    ),
    910006: (
        "OPPO_VERSION_EXISTS",
        "The OPPO version code already exists.",
        ExitCode.VENDOR_REJECTION,
    ),
    910007: (
        "OPPO_CATEGORY_INVALID",
        "The existing OPPO application category is invalid.",
        ExitCode.VENDOR_REJECTION,
    ),
    910008: (
        "OPPO_APPLICATION_NAME_EXISTS",
        "OPPO rejected the existing application name.",
        ExitCode.VENDOR_REJECTION,
    ),
    910009: (
        "OPPO_PACKAGE_MISMATCH",
        "OPPO rejected the package identity.",
        ExitCode.VENDOR_REJECTION,
    ),
    910010: (
        "OPPO_APK_SIGNATURE_MISMATCH",
        "OPPO rejected the APK signing identity.",
        ExitCode.VENDOR_REJECTION,
    ),
    910011: (
        "OPPO_APPLICATION_INCOMPLETE",
        "The existing OPPO application information is incomplete.",
        ExitCode.VENDOR_REJECTION,
    ),
    910012: (
        "OPPO_PERMISSION_DENIED",
        "The OPPO credential is not allowed to manage this application.",
        ExitCode.AUTHENTICATION,
    ),
    910013: (
        "OPPO_APPLICATION_NOT_APPROVED",
        "The OPPO application is not eligible for this update.",
        ExitCode.VENDOR_REJECTION,
    ),
    910014: (
        "OPPO_APPLICATION_FROZEN",
        "The OPPO application is frozen.",
        ExitCode.VENDOR_REJECTION,
    ),
    910015: (
        "OPPO_APPLICATION_OFFLINE",
        "The OPPO application is offline.",
        ExitCode.VENDOR_REJECTION,
    ),
}


def is_oppo_auth_failure(*, errno: int | None, status_code: int) -> bool:
    return errno == 910002 or status_code in {401, 403}


def parse_oppo_error(
    *,
    errno: int | None,
    status_code: int,
    vendor_message: object = None,
) -> OppoVendorError:
    """Map allowlisted codes without copying any vendor-controlled response text."""

    del vendor_message
    if errno is not None:
        code, message, exit_code = _ERRNO_ERRORS.get(
            errno,
            (
                "OPPO_API_REJECTED",
                "OPPO rejected the publishing request.",
                ExitCode.VENDOR_REJECTION,
            ),
        )
        return OppoVendorError(
            code,
            message,
            exit_code,
            vendor_code=str(errno),
            status_code=status_code,
        )
    if status_code in {401, 403}:
        return OppoVendorError(
            "OPPO_AUTHENTICATION_FAILED",
            "OPPO rejected the publishing credentials.",
            ExitCode.AUTHENTICATION,
            status_code=status_code,
        )
    if status_code == 429 or status_code >= 500:
        return OppoVendorError(
            "OPPO_SERVICE_UNAVAILABLE",
            f"OPPO publishing is temporarily unavailable (HTTP {status_code}).",
            ExitCode.NETWORK,
            status_code=status_code,
        )
    return OppoVendorError(
        "OPPO_API_REJECTED",
        f"OPPO rejected the publishing request (HTTP {status_code}).",
        ExitCode.VENDOR_REJECTION,
        status_code=status_code,
    )
