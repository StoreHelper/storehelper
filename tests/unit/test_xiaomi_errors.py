from __future__ import annotations

import pytest

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.xiaomi.errors import parse_xiaomi_error


@pytest.mark.parametrize(
    ("result", "code", "exit_code"),
    [
        (-7, "XIAOMI_PACKAGE_CLAIM_REQUIRED", ExitCode.VENDOR_REJECTION),
        (-2, "XIAOMI_PACKAGE_MISMATCH", ExitCode.VENDOR_REJECTION),
        (-32, "XIAOMI_PACKAGE_NOT_FOUND", ExitCode.VENDOR_REJECTION),
        (-92, "XIAOMI_APK_REJECTED", ExitCode.VENDOR_REJECTION),
        (-20014, "XIAOMI_AUTHENTICATION_FAILED", ExitCode.AUTHENTICATION),
        (-20002, "XIAOMI_SIGNATURE_REJECTED", ExitCode.AUTHENTICATION),
        (-20029, "XIAOMI_REQUEST_INVALID", ExitCode.VENDOR_REJECTION),
        (-20030, "XIAOMI_SIGNATURE_REJECTED", ExitCode.AUTHENTICATION),
        (-20034, "XIAOMI_TEST_ACCOUNT_INVALID", ExitCode.VENDOR_REJECTION),
        (-99999, "XIAOMI_API_REJECTED", ExitCode.VENDOR_REJECTION),
    ],
)
def test_xiaomi_documented_result_mapping_is_stable(
    result: int,
    code: str,
    exit_code: ExitCode,
) -> None:
    error = parse_xiaomi_error(result=result, status_code=200)

    assert error.code == code
    assert error.vendor_code == str(result)
    assert error.exit_code is exit_code


@pytest.mark.parametrize(
    ("status", "code", "exit_code"),
    [
        (401, "XIAOMI_AUTHENTICATION_FAILED", ExitCode.AUTHENTICATION),
        (403, "XIAOMI_AUTHENTICATION_FAILED", ExitCode.AUTHENTICATION),
        (429, "XIAOMI_NETWORK_ERROR", ExitCode.NETWORK),
        (503, "XIAOMI_NETWORK_ERROR", ExitCode.NETWORK),
        (400, "XIAOMI_API_REJECTED", ExitCode.VENDOR_REJECTION),
    ],
)
def test_xiaomi_http_status_mapping_is_stable(
    status: int,
    code: str,
    exit_code: ExitCode,
) -> None:
    error = parse_xiaomi_error(result=None, status_code=status)

    assert error.code == code
    assert error.exit_code is exit_code
    assert error.vendor_code is None


def test_xiaomi_errors_never_include_untrusted_vendor_body() -> None:
    leaked = "api_secret=must-never-print"

    error = parse_xiaomi_error(result=-20014, status_code=200, vendor_message=leaked)

    assert leaked not in str(error)
