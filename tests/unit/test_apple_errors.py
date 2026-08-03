from __future__ import annotations

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.apple.errors import parse_apple_errors


def test_parses_bounded_json_api_errors_without_leaking_secrets() -> None:
    secret = "private_key=never-print"

    error = parse_apple_errors(
        {
            "errors": [
                {
                    "status": "422",
                    "code": "ENTITY_ERROR.ATTRIBUTE.INVALID",
                    "title": "Invalid attribute",
                    "detail": secret,
                    "source": {"pointer": "/data/attributes/private"},
                    "meta": {"token": "also-secret"},
                }
            ]
        },
        status_code=422,
    )

    assert error.code == "APPLE_API_REJECTED"
    assert error.vendor_code == "ENTITY_ERROR.ATTRIBUTE.INVALID"
    assert error.exit_code is ExitCode.VENDOR_REJECTION
    assert "Invalid attribute" in str(error)
    assert secret not in str(error)
    assert "also-secret" not in str(error)


def test_malformed_error_payload_uses_generic_status_only() -> None:
    error = parse_apple_errors("access_token=never-print", status_code=403)

    assert error.code == "APPLE_AUTHORIZATION_FAILED"
    assert error.exit_code is ExitCode.AUTHENTICATION
    assert "403" in str(error)
    assert "never-print" not in str(error)
