"""Google OAuth 2.0 service-account authentication for Android Publisher."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from storehelper.credentials.models import GoogleServiceAccount
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.errors import StoreVendorError

ANDROID_PUBLISHER_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
JWT_BEARER_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current


class GoogleAuthError(StoreVendorError):
    """Sanitized token-exchange failure."""


class GoogleAuth:
    """Exchange signed RS256 assertions and cache access tokens in memory only."""

    def __init__(self, credential: GoogleServiceAccount, http: httpx.AsyncClient) -> None:
        self._credential = credential
        self._http = http
        self._cached_token: str | None = None
        self._expires_at: datetime | None = None
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return (
            f"GoogleAuth(project_id={self._credential.project_id!r}, "
            f"private_key_id={self._credential.private_key_id!r}, "
            f"client_email={self._credential.client_email!r})"
        )

    @staticmethod
    def _encode_json(value: Mapping[str, object]) -> str:
        return _base64url(
            json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        )

    def assertion(self, *, now: datetime | None = None) -> str:
        current = _utc(now)
        issued_at = int(current.timestamp())
        header = {
            "alg": "RS256",
            "kid": self._credential.private_key_id,
            "typ": "JWT",
        }
        payload = {
            "iss": self._credential.client_email,
            "scope": ANDROID_PUBLISHER_SCOPE,
            "aud": self._credential.token_uri,
            "iat": issued_at,
            "exp": issued_at + 3600,
        }
        encoded_header = self._encode_json(header)
        encoded_payload = self._encode_json(payload)
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        private_key: Any = serialization.load_pem_private_key(
            self._credential.private_key.get_secret_value().encode("utf-8"),
            password=None,
        )
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise TypeError("Google service accounts require an RSA private key.")
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{encoded_header}.{encoded_payload}.{_base64url(signature)}"

    def _cached(self, current: datetime, *, force_refresh: bool) -> str | None:
        if (
            not force_refresh
            and self._cached_token is not None
            and self._expires_at is not None
            and current < self._expires_at - timedelta(seconds=60)
        ):
            return self._cached_token
        return None

    @staticmethod
    def _error(code: str, message: str, exit_code: ExitCode) -> GoogleAuthError:
        return GoogleAuthError(code, message, exit_code)

    async def access_token(
        self,
        *,
        now: datetime | None = None,
        force_refresh: bool = False,
    ) -> str:
        current = _utc(now)
        cached = self._cached(current, force_refresh=force_refresh)
        if cached is not None:
            return cached

        async with self._lock:
            cached = self._cached(current, force_refresh=force_refresh)
            if cached is not None:
                return cached
            try:
                response = await self._http.post(
                    self._credential.token_uri,
                    data={
                        "grant_type": JWT_BEARER_GRANT_TYPE,
                        "assertion": self.assertion(now=current),
                    },
                    headers={"Accept": "application/json"},
                    follow_redirects=False,
                )
            except httpx.HTTPError:
                raise self._error(
                    "GOOGLE_AUTH_NETWORK_ERROR",
                    "Could not reach the Google OAuth token endpoint.",
                    ExitCode.NETWORK,
                ) from None
            if response.is_redirect:
                raise self._error(
                    "GOOGLE_AUTH_REDIRECT",
                    "Google OAuth returned an unexpected redirect.",
                    ExitCode.NETWORK,
                )
            if response.is_error:
                exit_code = (
                    ExitCode.NETWORK
                    if response.status_code == 429 or response.status_code >= 500
                    else ExitCode.AUTHENTICATION
                )
                raise self._error(
                    "GOOGLE_AUTH_REJECTED",
                    f"Google OAuth rejected the service account (HTTP {response.status_code}).",
                    exit_code,
                )
            try:
                payload = response.json()
            except (ValueError, UnicodeDecodeError):
                raise self._error(
                    "GOOGLE_AUTH_RESPONSE_INVALID",
                    "Google OAuth returned a non-JSON token response.",
                    ExitCode.AUTHENTICATION,
                ) from None
            if not isinstance(payload, Mapping):
                raise self._error(
                    "GOOGLE_AUTH_RESPONSE_INVALID",
                    "Google OAuth returned an invalid token response.",
                    ExitCode.AUTHENTICATION,
                )
            access_token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            token_type = payload.get("token_type", "Bearer")
            if (
                not isinstance(access_token, str)
                or not access_token
                or not isinstance(expires_in, int)
                or isinstance(expires_in, bool)
                or expires_in <= 0
                or not isinstance(token_type, str)
                or token_type.lower() != "bearer"
            ):
                raise self._error(
                    "GOOGLE_AUTH_RESPONSE_INVALID",
                    "Google OAuth token response is missing required fields.",
                    ExitCode.AUTHENTICATION,
                )
            self._cached_token = access_token
            self._expires_at = current + timedelta(seconds=expires_in)
            return access_token

    async def headers(
        self,
        *,
        now: datetime | None = None,
        force_refresh: bool = False,
    ) -> dict[str, str]:
        token = await self.access_token(now=now, force_refresh=force_refresh)
        return {"Authorization": f"Bearer {token}"}
