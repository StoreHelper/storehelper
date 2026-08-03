"""Deterministic OPPO request signing and memory-only token caching."""

from __future__ import annotations

import hashlib
import hmac
import math
import time
from collections.abc import Callable, Mapping

from pydantic import SecretStr

from storehelper.credentials.models import OppoApiCredential

_EXPIRY_SKEW_SECONDS = 300.0
_DEFAULT_TOKEN_LIFETIME_SECONDS = 170_000.0
_EPOCH_SECONDS_THRESHOLD = 1_000_000_000.0
_AUTH_FIELDS = frozenset({"access_token", "timestamp", "api_sign"})


def canonical_query(params: Mapping[str, object]) -> str:
    """Return OPPO's literal, ASCII-key-sorted HMAC input."""

    values: list[tuple[str, object]] = []
    for key, value in params.items():
        if not isinstance(key, str):
            raise ValueError("OPPO signing parameter names must be ASCII strings.")
        try:
            key.encode("ascii")
        except UnicodeEncodeError:
            raise ValueError("OPPO signing parameter names must be ASCII strings.") from None
        if key == "api_sign" or value is None or value == "":
            continue
        values.append((key, value))
    values.sort(key=lambda item: item[0].encode("ascii"))
    return "&".join(f"{key}={value}" for key, value in values)


def sign_params(secret: SecretStr, params: Mapping[str, object]) -> str:
    """Sign parameters using UTF-8 HMAC-SHA256 and lowercase hexadecimal output."""

    return hmac.new(
        secret.get_secret_value().encode("utf-8"),
        canonical_query(params).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class OppoAuth:
    """Secret-safe OPPO signing state with a memory-only token cache."""

    __slots__ = ("_clock", "_credential", "_expires_at", "_token")

    def __init__(
        self,
        credential: OppoApiCredential,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._credential = credential
        self._clock = clock
        self._token: SecretStr | None = None
        self._expires_at = 0.0

    def __repr__(self) -> str:
        return f"OppoAuth(configured=True, token_cached={self._token is not None})"

    __str__ = __repr__

    def cache_token(
        self,
        access_token: str,
        *,
        expires_in: int | float | str | None,
    ) -> None:
        """Cache a token whose expiry may be a relative duration or epoch timestamp."""

        if not isinstance(access_token, str) or not access_token.strip():
            raise ValueError("OPPO access token must not be empty.")
        if expires_in is None or expires_in == 0 or expires_in == "0":
            expiry = _DEFAULT_TOKEN_LIFETIME_SECONDS
        else:
            try:
                expiry = float(expires_in)
            except (TypeError, ValueError):
                raise ValueError("OPPO token expiry must be numeric.") from None
            if not math.isfinite(expiry) or expiry <= 0:
                raise ValueError("OPPO token expiry must be positive and finite.")
        now = self._clock()
        self._token = SecretStr(access_token.strip())
        self._expires_at = expiry if expiry >= _EPOCH_SECONDS_THRESHOLD else now + expiry

    def clear_token(self) -> None:
        self._token = None
        self._expires_at = 0.0

    def cached_token(self, *, force_refresh: bool = False) -> str | None:
        if force_refresh or self._token is None:
            return None
        if self._clock() >= self._expires_at - _EXPIRY_SKEW_SECONDS:
            self.clear_token()
            return None
        return self._token.get_secret_value()

    def signed_params(self, business_params: Mapping[str, object] | None = None) -> dict[str, str]:
        token = self.cached_token()
        if token is None:
            raise RuntimeError("OPPO signing requires a fresh access token.")
        params: dict[str, object] = {
            key: value
            for key, value in (business_params or {}).items()
            if key not in _AUTH_FIELDS and value is not None and value != ""
        }
        params["access_token"] = token
        params["timestamp"] = str(int(self._clock()))
        params["api_sign"] = sign_params(self._credential.client_secret, params)
        return {key: str(value) for key, value in params.items()}
