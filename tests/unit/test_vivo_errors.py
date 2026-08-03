from __future__ import annotations

import pytest

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.vivo.errors import VivoVendorError, parse_vivo_error


@pytest.mark.parametrize(
    ("code", "sub_code", "expected", "exit_code"),
    [
        ("0", "B0302", "VIVO_UPDATE_CONFLICT", ExitCode.VENDOR_REJECTION),
        ("0", "20008", "VIVO_REQUEST_INVALID", ExitCode.VENDOR_REJECTION),
        ("AUTH", None, "VIVO_AUTHENTICATION_FAILED", ExitCode.AUTHENTICATION),
    ],
)
def test_known_vivo_codes_are_mapped_without_vendor_text(
    code: str,
    sub_code: str | None,
    expected: str,
    exit_code: ExitCode,
) -> None:
    error = parse_vivo_error(
        code=code,
        sub_code=sub_code,
        status_code=200,
        vendor_message="secret_key=must-not-leak",
    )

    assert error.code == expected
    assert error.exit_code is exit_code
    assert "must-not-leak" not in str(error)
    assert isinstance(error, VivoVendorError)


def test_unknown_vivo_and_http_failures_are_stable_and_safe() -> None:
    unknown = parse_vivo_error(
        code="999999",
        sub_code="UNKNOWN",
        status_code=200,
        vendor_message={"access_key": "must-not-leak"},
    )
    unauthorized = parse_vivo_error(code=None, sub_code=None, status_code=403)
    transient = parse_vivo_error(code=None, sub_code=None, status_code=503)

    assert unknown.code == "VIVO_API_REJECTED"
    assert unknown.vendor_code == "999999/UNKNOWN"
    assert "must-not-leak" not in str(unknown)
    assert unauthorized.code == "VIVO_AUTHENTICATION_FAILED"
    assert unauthorized.exit_code is ExitCode.AUTHENTICATION
    assert transient.code == "VIVO_SERVICE_UNAVAILABLE"
    assert transient.exit_code is ExitCode.NETWORK
