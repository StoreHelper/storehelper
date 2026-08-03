from __future__ import annotations

import re

import pytest
from pydantic import SecretStr

from storehelper.credentials.models import OppoApiCredential
from storehelper.stores.oppo.auth import OppoAuth, canonical_query, sign_params


def credential() -> OppoApiCredential:
    return OppoApiCredential(
        client_id=SecretStr("oppo-client-sensitive"),
        client_secret=SecretStr("秘密"),
    )


def test_canonical_query_uses_ascii_order_and_literal_unencoded_values() -> None:
    params: dict[str, object] = {
        "z": "last",
        "access_token": "令牌",
        "empty": "",
        "none": None,
        "api_sign": "must-not-participate",
        "zero": 0,
        "a": "first",
    }

    assert canonical_query(params) == "a=first&access_token=令牌&z=last&zero=0"
    assert params["api_sign"] == "must-not-participate"


def test_sign_params_matches_utf8_hmac_sha256_lowercase_vector() -> None:
    signature = sign_params(
        SecretStr("秘密"),
        {
            "z": "last",
            "access_token": "令牌",
            "zero": 0,
            "a": "first",
        },
    )

    assert signature == "f0d92d2af6ef5d3bd81b8e51caa3a7a27e46e5e678f239e1f23c54df27c8844e"
    assert re.fullmatch(r"[0-9a-f]{64}", signature)


def test_canonical_query_rejects_non_ascii_parameter_names() -> None:
    with pytest.raises(ValueError, match="ASCII"):
        canonical_query({"参数": "value"})


def test_auth_builds_deterministic_signed_params_without_mutating_input() -> None:
    now = 1_700_000_000.9
    auth = OppoAuth(credential(), clock=lambda: now)
    auth.cache_token("oppo-access-sensitive", expires_in=7200)
    business: dict[str, object] = {
        "pkg_name": "com.example.wallet",
        "api_sign": "ignored",
        "access_token": "cannot-override",
        "timestamp": "cannot-override",
    }

    params = auth.signed_params(business)

    assert business["api_sign"] == "ignored"
    assert params["access_token"] == "oppo-access-sensitive"
    assert params["timestamp"] == "1700000000"
    assert params["pkg_name"] == "com.example.wallet"
    assert params["api_sign"] == sign_params(SecretStr("秘密"), params)


def test_auth_cache_accepts_relative_and_absolute_expiry_and_refresh() -> None:
    now = [1_700_000_000.0]
    auth = OppoAuth(credential(), clock=lambda: now[0])

    auth.cache_token("relative-token", expires_in="7200")
    assert auth.cached_token() == "relative-token"
    assert auth.cached_token(force_refresh=True) is None

    auth.cache_token("absolute-token", expires_in=1_700_010_000)
    now[0] = 1_700_009_699
    assert auth.cached_token() == "absolute-token"
    now[0] = 1_700_009_700
    assert auth.cached_token() is None


def test_auth_cache_uses_safe_default_for_missing_vendor_expiry() -> None:
    now = [1_700_000_000.0]
    auth = OppoAuth(credential(), clock=lambda: now[0])

    auth.cache_token("default-token", expires_in=None)
    now[0] += 169_699
    assert auth.cached_token() == "default-token"
    now[0] += 1
    assert auth.cached_token() is None


def test_auth_rejects_invalid_token_or_expiry_without_echoing_values() -> None:
    auth = OppoAuth(credential(), clock=lambda: 1_700_000_000.0)

    with pytest.raises(ValueError) as empty:
        auth.cache_token(" ", expires_in=100)
    with pytest.raises(ValueError) as expiry:
        auth.cache_token("token-that-must-not-leak", expires_in="not-a-number")

    assert "token-that-must-not-leak" not in str(expiry.value)
    assert "oppo-client-sensitive" not in str(empty.value) + str(expiry.value)
    assert "秘密" not in str(empty.value) + str(expiry.value)


def test_auth_repr_never_exposes_credentials_or_cached_token() -> None:
    auth = OppoAuth(credential(), clock=lambda: 1_700_000_000.0)
    auth.cache_token("oppo-access-sensitive", expires_in=7200)

    rendered = repr(auth) + str(auth)

    assert "oppo-client-sensitive" not in rendered
    assert "秘密" not in rendered
    assert "oppo-access-sensitive" not in rendered
    assert "token_cached=True" in rendered


def test_signed_params_requires_a_fresh_cached_token() -> None:
    auth = OppoAuth(credential(), clock=lambda: 1_700_000_000.0)

    with pytest.raises(RuntimeError, match="fresh access token"):
        auth.signed_params({"pkg_name": "com.example.wallet"})
