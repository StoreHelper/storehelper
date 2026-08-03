from __future__ import annotations

import re

import pytest
from pydantic import SecretStr

from storehelper.credentials.models import VivoApiCredential
from storehelper.stores.vivo.auth import VivoAuth, canonical_query, sign_params


def credential() -> VivoApiCredential:
    return VivoApiCredential(
        access_key=SecretStr("vivo-access-sensitive"),
        secret_key=SecretStr("秘密"),
    )


def test_vivo_canonical_query_uses_ascii_order_and_literal_values() -> None:
    params: dict[str, object] = {
        "z": "last",
        "access_key": "访问值",
        "empty": "",
        "none": None,
        "sign": "must-not-participate",
        "zero": 0,
        "a": "first",
    }

    assert canonical_query(params) == "a=first&access_key=访问值&z=last&zero=0"
    assert params["sign"] == "must-not-participate"


def test_vivo_sign_params_matches_utf8_hmac_sha256_lowercase_vector() -> None:
    signature = sign_params(
        SecretStr("秘密"),
        {"z": "last", "access_key": "访问值", "zero": 0, "a": "first"},
    )

    assert signature == "4bf144bd9e953d1f645c4587a9c5fe65d6ef6ff4b89cae9108aee6ad5e2de23a"
    assert re.fullmatch(r"[0-9a-f]{64}", signature)


def test_vivo_canonical_query_rejects_non_ascii_parameter_names() -> None:
    with pytest.raises(ValueError, match="ASCII"):
        canonical_query({"参数": "value"})


def test_vivo_auth_builds_exact_common_params_without_mutating_business() -> None:
    auth = VivoAuth(credential(), clock=lambda: 1_700_000_000.987)
    business: dict[str, object] = {
        "packageName": "com.example.wallet",
        "sign": "ignored",
        "access_key": "cannot-override",
        "timestamp": "cannot-override",
        "method": "cannot-override",
    }

    params = auth.signed_params("app.query.details", business)

    assert business["sign"] == "ignored"
    assert params == {
        "packageName": "com.example.wallet",
        "method": "app.query.details",
        "access_key": "vivo-access-sensitive",
        "timestamp": "1700000000987",
        "format": "json",
        "v": "1.0",
        "sign_method": "HMAC-SHA256",
        "target_app_key": "developer",
        "sign": sign_params(SecretStr("秘密"), params),
    }


def test_vivo_auth_repr_never_exposes_credentials() -> None:
    auth = VivoAuth(credential())

    rendered = repr(auth) + str(auth)

    assert "vivo-access-sensitive" not in rendered
    assert "秘密" not in rendered
    assert rendered == "VivoAuth(configured=True)VivoAuth(configured=True)"
