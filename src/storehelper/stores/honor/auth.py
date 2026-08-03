"""Fixed-host HONOR client-credentials authentication."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping

import httpx
from pydantic import SecretStr

from storehelper.credentials.models import HonorApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.honor.errors import HonorVendorError

HONOR_TOKEN_URL = "https://iam.developer.honor.com/auth/token"
MAX_TOKEN_RESPONSE_BYTES = 1024 * 1024
_EXPIRY_SKEW_SECONDS = 60.0


class HonorAuth:
    """Acquire and cache an account token without exposing the credential pair."""

    def __init__(
        self,
        credential: HonorApiCredential,
        http: httpx.AsyncClient,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._credential = credential
        self._http = http
        self._clock = clock
        self._token: SecretStr | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"HonorAuth(configured=True, token_cached={self._token is not None})"

    __str__ = __repr__

    def _cached(self, *, force_refresh: bool) -> str | None:
        if (
            force_refresh
            or self._token is None
            or self._clock() >= self._expires_at - _EXPIRY_SKEW_SECONDS
        ):
            return None
        return self._token.get_secret_value()

    @staticmethod
    def _error(code: str, message: str, exit_code: ExitCode) -> HonorVendorError:
        return HonorVendorError(code, message, exit_code)

    async def access_token(self, *, force_refresh: bool = False) -> str:
        cached = self._cached(force_refresh=force_refresh)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._cached(force_refresh=force_refresh)
            if cached is not None:
                return cached
            try:
                response = await self._http.post(
                    HONOR_TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._credential.client_id.get_secret_value(),
                        "client_secret": self._credential.client_secret.get_secret_value(),
                    },
                    headers={"Accept": "application/json"},
                    follow_redirects=False,
                )
            except (OSError, httpx.HTTPError):
                raise self._error(
                    "HONOR_AUTH_NETWORK_ERROR",
                    "Could not reach the HONOR token endpoint.",
                    ExitCode.NETWORK,
                ) from None
            if response.is_redirect:
                raise self._error(
                    "HONOR_AUTH_REDIRECT",
                    "HONOR authentication returned an unexpected redirect.",
                    ExitCode.NETWORK,
                )
            if len(response.content) > MAX_TOKEN_RESPONSE_BYTES:
                raise self._error(
                    "HONOR_AUTH_RESPONSE_INVALID",
                    "HONOR returned an oversized token response.",
                    ExitCode.AUTHENTICATION,
                )
            if response.is_error:
                exit_code = (
                    ExitCode.NETWORK
                    if response.status_code == 429 or response.status_code >= 500
                    else ExitCode.AUTHENTICATION
                )
                raise self._error(
                    "HONOR_AUTH_REJECTED",
                    f"HONOR rejected the API credential (HTTP {response.status_code}).",
                    exit_code,
                )
            try:
                payload = response.json()
            except (ValueError, UnicodeDecodeError):
                raise self._error(
                    "HONOR_AUTH_RESPONSE_INVALID",
                    "HONOR returned a non-JSON token response.",
                    ExitCode.AUTHENTICATION,
                ) from None
            if not isinstance(payload, Mapping):
                raise self._error(
                    "HONOR_AUTH_RESPONSE_INVALID",
                    "HONOR returned an invalid token response.",
                    ExitCode.AUTHENTICATION,
                )
            token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            token_type = payload.get("token_type")
            if (
                not isinstance(token, str)
                or not token.strip()
                or not isinstance(expires_in, int)
                or isinstance(expires_in, bool)
                or expires_in <= 0
                or not isinstance(token_type, str)
                or token_type.lower() != "bearer"
            ):
                raise self._error(
                    "HONOR_AUTH_RESPONSE_INVALID",
                    "HONOR token response is missing required fields.",
                    ExitCode.AUTHENTICATION,
                )
            self._token = SecretStr(token.strip())
            self._expires_at = self._clock() + expires_in
            return token.strip()

    async def headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        token = await self.access_token(force_refresh=force_refresh)
        return {"Authorization": f"Bearer {token}"}
