from __future__ import annotations

import pytest

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.oppo.errors import OppoVendorError, parse_oppo_error


@pytest.mark.parametrize(
    ("errno", "code", "exit_code"),
    [
        (910000, "OPPO_SERVICE_UNAVAILABLE", ExitCode.NETWORK),
        (910001, "OPPO_REQUEST_INVALID", ExitCode.VENDOR_REJECTION),
        (910002, "OPPO_AUTHENTICATION_FAILED", ExitCode.AUTHENTICATION),
        (910003, "OPPO_APPLICATION_NOT_FOUND", ExitCode.VENDOR_REJECTION),
        (911023, "OPPO_APPLICATION_NOT_FOUND", ExitCode.VENDOR_REJECTION),
        (910009, "OPPO_PACKAGE_MISMATCH", ExitCode.VENDOR_REJECTION),
        (910011, "OPPO_APPLICATION_INCOMPLETE", ExitCode.VENDOR_REJECTION),
        (910012, "OPPO_PERMISSION_DENIED", ExitCode.AUTHENTICATION),
        (910014, "OPPO_APPLICATION_FROZEN", ExitCode.VENDOR_REJECTION),
        (910015, "OPPO_APPLICATION_OFFLINE", ExitCode.VENDOR_REJECTION),
    ],
)
def test_known_oppo_errno_is_mapped_to_stable_safe_error(
    errno: int,
    code: str,
    exit_code: ExitCode,
) -> None:
    error = parse_oppo_error(
        errno=errno,
        status_code=200,
        vendor_message="client_secret=must-not-leak",
    )

    assert error.code == code
    assert error.exit_code is exit_code
    assert error.vendor_code == str(errno)
    assert "must-not-leak" not in str(error)


def test_unknown_errno_and_http_failures_are_safe() -> None:
    unknown = parse_oppo_error(
        errno=999999,
        status_code=200,
        vendor_message={"access_token": "must-not-leak"},
    )
    unauthorized = parse_oppo_error(errno=None, status_code=403)
    transient = parse_oppo_error(errno=None, status_code=503)

    assert unknown.code == "OPPO_API_REJECTED"
    assert unknown.vendor_code == "999999"
    assert "must-not-leak" not in str(unknown)
    assert unauthorized.code == "OPPO_AUTHENTICATION_FAILED"
    assert unauthorized.exit_code is ExitCode.AUTHENTICATION
    assert transient.code == "OPPO_SERVICE_UNAVAILABLE"
    assert transient.exit_code is ExitCode.NETWORK
    assert isinstance(unknown, OppoVendorError)
