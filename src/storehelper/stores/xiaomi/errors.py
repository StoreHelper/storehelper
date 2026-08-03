"""Stable, secret-safe Xiaomi publishing errors."""

from __future__ import annotations

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.errors import StoreVendorError


class XiaomiVendorError(StoreVendorError):
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


_RESULT_ERRORS: dict[int, tuple[str, str, ExitCode]] = {
    -7: (
        "XIAOMI_PACKAGE_CLAIM_REQUIRED",
        "The package belongs to another Xiaomi developer account and must be claimed first.",
        ExitCode.VENDOR_REJECTION,
    ),
    -2: (
        "XIAOMI_PACKAGE_MISMATCH",
        "Xiaomi rejected the package-name identity.",
        ExitCode.VENDOR_REJECTION,
    ),
    -32: (
        "XIAOMI_PACKAGE_NOT_FOUND",
        "The package must already exist in the Xiaomi developer console.",
        ExitCode.VENDOR_REJECTION,
    ),
    -92: (
        "XIAOMI_APK_REJECTED",
        "Xiaomi rejected the APK version or package.",
        ExitCode.VENDOR_REJECTION,
    ),
    -20014: (
        "XIAOMI_AUTHENTICATION_FAILED",
        "Xiaomi rejected the automatic-publishing API secret.",
        ExitCode.AUTHENTICATION,
    ),
    -20002: (
        "XIAOMI_SIGNATURE_REJECTED",
        "Xiaomi rejected the encrypted request signature.",
        ExitCode.AUTHENTICATION,
    ),
    -20029: (
        "XIAOMI_REQUEST_INVALID",
        "Xiaomi rejected the RequestData JSON.",
        ExitCode.VENDOR_REJECTION,
    ),
    -20030: (
        "XIAOMI_SIGNATURE_REJECTED",
        "Xiaomi rejected the encrypted request signature.",
        ExitCode.AUTHENTICATION,
    ),
    -20034: (
        "XIAOMI_TEST_ACCOUNT_INVALID",
        "Xiaomi rejected the structured review accounts.",
        ExitCode.VENDOR_REJECTION,
    ),
}


def parse_xiaomi_error(
    *,
    result: int | None,
    status_code: int,
    vendor_message: object = None,
) -> XiaomiVendorError:
    """Map only trusted numeric codes; never include the vendor body or message."""

    del vendor_message
    if result is not None:
        code, message, exit_code = _RESULT_ERRORS.get(
            result,
            (
                "XIAOMI_API_REJECTED",
                "Xiaomi rejected the publishing request.",
                ExitCode.VENDOR_REJECTION,
            ),
        )
        return XiaomiVendorError(
            code,
            message,
            exit_code,
            vendor_code=str(result),
            status_code=status_code,
        )
    if status_code in {401, 403}:
        return XiaomiVendorError(
            "XIAOMI_AUTHENTICATION_FAILED",
            "Xiaomi rejected the publishing credentials.",
            ExitCode.AUTHENTICATION,
            status_code=status_code,
        )
    if status_code == 429 or status_code >= 500:
        return XiaomiVendorError(
            "XIAOMI_NETWORK_ERROR",
            f"Xiaomi publishing is temporarily unavailable (HTTP {status_code}).",
            ExitCode.NETWORK,
            status_code=status_code,
        )
    return XiaomiVendorError(
        "XIAOMI_API_REJECTED",
        f"Xiaomi rejected the publishing request (HTTP {status_code}).",
        ExitCode.VENDOR_REJECTION,
        status_code=status_code,
    )
