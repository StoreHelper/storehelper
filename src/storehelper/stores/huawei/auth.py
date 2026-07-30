"""Huawei Service Account PS256 authentication."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from storehelper.credentials.models import HuaweiServiceAccount


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class HuaweiAuth:
    """Generate and cache Huawei bearer JWTs in memory only."""

    def __init__(self, account: HuaweiServiceAccount) -> None:
        self._account = account
        self._cached_token: str | None = None
        self._expires_at: datetime | None = None

    def __repr__(self) -> str:
        return (
            f"HuaweiAuth(key_id={self._account.key_id!r}, "
            f"sub_account={self._account.sub_account!r})"
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
        header = {"alg": "PS256", "kid": self._account.key_id, "typ": "JWT"}
        payload = {
            "aud": self._account.token_uri,
            "iss": self._account.sub_account,
            "iat": issued_at,
            "exp": issued_at + 3600,
        }
        encoded_header = self._encode_json(header)
        encoded_payload = self._encode_json(payload)
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        private_key: Any = serialization.load_pem_private_key(
            self._account.private_key.get_secret_value().encode("utf-8"),
            password=None,
        )
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise TypeError("Huawei Service Account requires an RSA private key.")
        signature = private_key.sign(
            signing_input,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256.digest_size,
            ),
            hashes.SHA256(),
        )
        self._cached_token = f"{encoded_header}.{encoded_payload}.{_base64url(signature)}"
        self._expires_at = current + timedelta(seconds=3600)
        return self._cached_token

    def headers(
        self,
        *,
        now: datetime | None = None,
        force_refresh: bool = False,
    ) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token(now=now, force_refresh=force_refresh)}",
            "client_id": self._account.key_id,
        }

    @staticmethod
    def _encode_json(value: Mapping[str, object]) -> str:
        return _base64url(
            json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        )
