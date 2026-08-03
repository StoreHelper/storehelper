"""App Store Connect ES256 bearer authentication."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from storehelper.credentials.models import AppleApiKey, AppleKeyType


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class AppleAuth:
    """Generate ten-minute App Store Connect JWTs and cache them in memory only."""

    def __init__(self, credential: AppleApiKey) -> None:
        self._credential = credential
        self._cached_token: str | None = None
        self._expires_at: datetime | None = None

    def __repr__(self) -> str:
        return (
            f"AppleAuth(key_type={self._credential.key_type.value!r}, "
            f"key_id={self._credential.key_id!r})"
        )

    def token(self, *, now: datetime | None = None, force_refresh: bool = False) -> str:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        if (
            not force_refresh
            and self._cached_token is not None
            and self._expires_at is not None
            and current < self._expires_at - timedelta(seconds=60)
        ):
            return self._cached_token

        issued_at = int(current.timestamp())
        header = {"alg": "ES256", "kid": self._credential.key_id, "typ": "JWT"}
        payload: dict[str, object] = {
            "iat": issued_at,
            "exp": issued_at + 600,
            "aud": "appstoreconnect-v1",
        }
        if self._credential.key_type is AppleKeyType.TEAM:
            assert self._credential.issuer_id is not None
            payload["iss"] = self._credential.issuer_id
        else:
            payload["sub"] = "user"

        encoded_header = self._encode_json(header)
        encoded_payload = self._encode_json(payload)
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        private_key: Any = serialization.load_pem_private_key(
            self._credential.private_key.get_secret_value().encode("utf-8"),
            password=None,
        )
        if not isinstance(private_key, ec.EllipticCurvePrivateKey):
            raise TypeError("Apple API keys require an EC private key.")
        der_signature = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der_signature)
        raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        self._cached_token = f"{encoded_header}.{encoded_payload}.{_base64url(raw_signature)}"
        self._expires_at = current + timedelta(seconds=600)
        return self._cached_token

    def headers(
        self,
        *,
        now: datetime | None = None,
        force_refresh: bool = False,
    ) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(now=now, force_refresh=force_refresh)}"}

    @staticmethod
    def _encode_json(value: Mapping[str, object]) -> str:
        return _base64url(
            json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        )
