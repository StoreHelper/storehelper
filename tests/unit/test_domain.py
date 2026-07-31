import pytest
from pydantic import ValidationError

from storehelper.domain.errors import StoreHelperError, redact
from storehelper.domain.exit_codes import ExitCode
from storehelper.domain.models import OperationResult, PublishRequest, PublishStage


def test_operation_result_serializes_stable_schema() -> None:
    result = OperationResult.success(
        stage=PublishStage.SUBMITTED,
        run_id="run-1",
    )

    assert result.model_dump(mode="json") == {
        "schema_version": 1,
        "ok": True,
        "run_id": "run-1",
        "store": "huawei",
        "stage": "submitted",
        "resumable": False,
        "message": "Huawei accepted the review submission.",
        "vendor": None,
        "next_action": None,
    }


def test_failure_result_contains_safe_next_action() -> None:
    result = OperationResult.failure(
        stage=PublishStage.PACKAGE_COMPILING,
        run_id="run-2",
        message="Huawei is still compiling the package.",
        resumable=True,
        vendor_code="204144727",
    )

    assert result.ok is False
    assert result.vendor is not None
    assert result.vendor.code == "204144727"
    assert result.next_action is not None
    assert result.next_action.command == "storehelper resume run-2"


def test_redact_removes_pem_jwt_authorization_and_auth_code() -> None:
    value = (
        "Authorization: Bearer aaa.bbb.ccc\n"
        "authCode=upload-secret\n"
        "private_key=field-secret\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "pem-secret\n"
        "-----END PRIVATE KEY-----"
    )

    redacted = redact(value)

    for secret in ("aaa.bbb.ccc", "upload-secret", "field-secret", "pem-secret"):
        assert secret not in redacted
    assert "[REDACTED]" in redacted


def test_storehelper_error_redacts_message_and_maps_exit_code() -> None:
    error = StoreHelperError(
        code="AUTH_FAILED",
        message="Authorization: Bearer aaa.bbb.ccc",
        exit_code=ExitCode.AUTHENTICATION,
    )

    assert "aaa.bbb.ccc" not in str(error)
    assert error.exit_code == ExitCode.AUTHENTICATION


def test_publish_timeout_cannot_be_shorter_than_poll_interval() -> None:
    with pytest.raises(ValidationError):
        PublishRequest(
            app_alias="demo",
            file="release.apk",
            poll_interval_seconds=30,
            wait_timeout_seconds=10,
        )
