from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from storehelper.credentials.models import AppleApiKey
from storehelper.stores.apple.auth import AppleAuth


def _decode(segment: str) -> dict[str, object]:
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def _credential(private_key: str, *, individual: bool = False) -> AppleApiKey:
    values: dict[str, str] = {
        "key_type": "individual" if individual else "team",
        "key_id": "APPLEKEY1",
        "private_key": private_key,
    }
    if not individual:
        values["issuer_id"] = "issuer-1"
    return AppleApiKey.model_validate(values)


def test_team_jwt_has_expected_claims_and_valid_raw_es256_signature(
    p256_private_key: str,
) -> None:
    now = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)

    token = AppleAuth(_credential(p256_private_key)).token(now=now)
    encoded_header, encoded_payload, encoded_signature = token.split(".")

    assert _decode(encoded_header) == {"alg": "ES256", "kid": "APPLEKEY1", "typ": "JWT"}
    assert _decode(encoded_payload) == {
        "iss": "issuer-1",
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + 600,
        "aud": "appstoreconnect-v1",
    }
    raw = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
    assert len(raw) == 64
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    private_key = serialization.load_pem_private_key(p256_private_key.encode(), password=None)
    assert isinstance(private_key, ec.EllipticCurvePrivateKey)
    private_key.public_key().verify(
        der,
        f"{encoded_header}.{encoded_payload}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )


def test_individual_jwt_uses_sub_user_and_tokens_are_cached(p256_private_key: str) -> None:
    auth = AppleAuth(_credential(p256_private_key, individual=True))
    now = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)

    first = auth.token(now=now)
    cached = auth.token(now=now + timedelta(seconds=539))
    renewed = auth.token(now=now + timedelta(seconds=541))

    payload = _decode(first.split(".")[1])
    assert payload["sub"] == "user"
    assert "iss" not in payload
    assert cached == first
    assert renewed != first
    assert p256_private_key not in repr(auth)
