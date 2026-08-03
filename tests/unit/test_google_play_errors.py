from __future__ import annotations

import pytest

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.google_play.errors import parse_google_error


def test_parses_bounded_google_error_without_leaking_nested_secrets() -> None:
    secret = "access_token=must-never-print"

    error = parse_google_error(
        {
            "error": {
                "code": 403,
                "message": f"Permission denied: {secret}",
                "status": "PERMISSION_DENIED",
                "details": [{"private_key": "also-secret"}],
            }
        },
        status_code=403,
    )

    assert error.code == "GOOGLE_AUTHORIZATION_FAILED"
    assert error.vendor_code == "PERMISSION_DENIED"
    assert error.exit_code is ExitCode.AUTHENTICATION
    assert "Permission denied" in str(error)
    assert secret not in str(error)
    assert "also-secret" not in str(error)


@pytest.mark.parametrize(
    ("status_code", "code", "exit_code"),
    [
        (401, "GOOGLE_AUTHORIZATION_FAILED", ExitCode.AUTHENTICATION),
        (404, "GOOGLE_RESOURCE_NOT_FOUND", ExitCode.VENDOR_REJECTION),
        (409, "GOOGLE_API_REJECTED", ExitCode.VENDOR_REJECTION),
        (429, "GOOGLE_NETWORK_ERROR", ExitCode.NETWORK),
        (503, "GOOGLE_NETWORK_ERROR", ExitCode.NETWORK),
    ],
)
def test_google_error_status_mapping_is_stable(
    status_code: int,
    code: str,
    exit_code: ExitCode,
) -> None:
    error = parse_google_error("private_key=must-never-print", status_code=status_code)

    assert error.code == code
    assert error.exit_code is exit_code
    assert "must-never-print" not in str(error)


def test_rejects_untrusted_vendor_code() -> None:
    error = parse_google_error(
        {"error": {"status": "../../SECRET", "message": "invalid"}},
        status_code=400,
    )

    assert error.vendor_code is None
