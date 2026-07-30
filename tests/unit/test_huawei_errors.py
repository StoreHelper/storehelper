from __future__ import annotations

import pytest

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.huawei.errors import (
    HuaweiVendorError,
    parse_huawei_response,
    translate_huawei_error,
)


@pytest.mark.parametrize(
    ("payload", "code", "message"),
    [
        ({"ret": {"code": 204144727, "msg": "compiling"}}, "204144727", "compiling"),
        (
            {"ret": '{"code":204144662,"msg":"bad name"}'},
            "204144662",
            "bad name",
        ),
        ({"ret": {"code": 0, "msg": "success"}}, "0", "success"),
    ],
)
def test_parses_object_and_string_ret(payload: dict[str, object], code: str, message: str) -> None:
    parsed = parse_huawei_response(payload)

    assert parsed.code == code
    assert parsed.message == message


def test_compiling_error_is_resumable() -> None:
    error = translate_huawei_error("204144727", "package compiling")

    assert isinstance(error, HuaweiVendorError)
    assert error.code == "HUAWEI_PACKAGE_COMPILING"
    assert error.vendor_code == "204144727"
    assert error.resumable is True
    assert error.exit_code == ExitCode.RESUMABLE_TIMEOUT


def test_authentication_error_uses_auth_exit_code() -> None:
    error = translate_huawei_error("204144665", "not authorized")

    assert error.code == "HUAWEI_AUTH_FAILED"
    assert error.exit_code == ExitCode.AUTHENTICATION


def test_unknown_error_is_bounded_and_redacted() -> None:
    error = translate_huawei_error("999", "Authorization: Bearer aaa.bbb.ccc " + "x" * 600)

    assert error.code == "HUAWEI_REJECTED"
    assert "aaa.bbb.ccc" not in str(error)
    assert len(str(error)) <= 550


def test_missing_ret_is_a_protocol_error() -> None:
    with pytest.raises(HuaweiVendorError) as raised:
        parse_huawei_response({"unexpected": True})

    assert raised.value.code == "HUAWEI_RESPONSE_INVALID"
