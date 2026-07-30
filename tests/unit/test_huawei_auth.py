from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from storehelper.credentials.models import HuaweiServiceAccount
from storehelper.stores.huawei.auth import HuaweiAuth


def decode_segment(segment: str) -> dict[str, object]:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def make_account(private_key: str) -> HuaweiServiceAccount:
    return HuaweiServiceAccount.model_validate(
        {
            "key_id": "kid-1",
            "sub_account": "sub-1",
            "private_key": private_key,
        }
    )


def test_jwt_uses_ps256_expected_claims_and_key_id(rsa_private_key: str) -> None:
    now = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)

    token = HuaweiAuth(make_account(rsa_private_key)).token(now=now)
    encoded_header, encoded_payload, encoded_signature = token.split(".")

    assert decode_segment(encoded_header) == {"alg": "PS256", "kid": "kid-1", "typ": "JWT"}
    payload = decode_segment(encoded_payload)
    assert payload == {
        "aud": "https://oauth-login.cloud.huawei.com/oauth2/v3/token",
        "iss": "sub-1",
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + 3600,
    }

    signature = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
    private_key = serialization.load_pem_private_key(rsa_private_key.encode(), password=None)
    private_key.public_key().verify(
        signature,
        f"{encoded_header}.{encoded_payload}".encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )


def test_token_is_cached_until_renewal_margin(rsa_private_key: str) -> None:
    auth = HuaweiAuth(make_account(rsa_private_key))
    now = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)

    first = auth.token(now=now)
    cached = auth.token(now=now + timedelta(seconds=3539))
    renewed = auth.token(now=now + timedelta(seconds=3541))

    assert cached == first
    assert renewed != first


def test_headers_use_key_id_as_android_client_id(rsa_private_key: str) -> None:
    auth = HuaweiAuth(make_account(rsa_private_key))

    headers = auth.headers(now=datetime(2026, 7, 30, 8, 0, tzinfo=UTC))

    assert headers["client_id"] == "kid-1"
    assert headers["Authorization"].startswith("Bearer ")
    assert rsa_private_key not in repr(auth)
