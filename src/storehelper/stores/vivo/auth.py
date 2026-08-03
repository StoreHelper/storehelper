"""Deterministic vivo HMAC request authentication."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable, Mapping

from pydantic import SecretStr

from storehelper.credentials.models import VivoApiCredential

_COMMON_FIELDS = frozenset(
    {
        "method",
        "access_key",
        "timestamp",
        "format",
        "v",
        "sign_method",
        "target_app_key",
        "sign",
    }
)


def canonical_query(params: Mapping[str, object]) -> str:
    """Return vivo's literal, ASCII-key-sorted HMAC input."""

    values: list[tuple[str, object]] = []
    for key, value in params.items():
        if not isinstance(key, str):
            raise ValueError("vivo signing parameter names must be ASCII strings.")
        try:
            key.encode("ascii")
        except UnicodeEncodeError:
            raise ValueError("vivo signing parameter names must be ASCII strings.") from None
        if key == "sign" or value is None or value == "":
            continue
        values.append((key, value))
    values.sort(key=lambda item: item[0].encode("ascii"))
    return "&".join(f"{key}={value}" for key, value in values)


def sign_params(secret: SecretStr, params: Mapping[str, object]) -> str:
    """Sign vivo parameters using UTF-8 HMAC-SHA256 lowercase hexadecimal."""

    return hmac.new(
        secret.get_secret_value().encode("utf-8"),
        canonical_query(params).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class VivoAuth:
    """Secret-safe creator for vivo's complete signed request parameters."""

    __slots__ = ("_clock", "_credential")

    def __init__(
        self,
        credential: VivoApiCredential,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._credential = credential
        self._clock = clock

    def __repr__(self) -> str:
        return "VivoAuth(configured=True)"

    __str__ = __repr__

    def signed_params(
        self,
        method: str,
        business_params: Mapping[str, object] | None = None,
    ) -> dict[str, str]:
        if not isinstance(method, str) or not method.strip() or len(method.strip()) > 128:
            raise ValueError("vivo API method must contain 1 to 128 characters.")
        params: dict[str, object] = {
            key: value
            for key, value in (business_params or {}).items()
            if key not in _COMMON_FIELDS and value is not None and value != ""
        }
        params.update(
            {
                "method": method.strip(),
                "access_key": self._credential.access_key.get_secret_value(),
                "timestamp": str(int(self._clock() * 1000)),
                "format": "json",
                "v": "1.0",
                "sign_method": "HMAC-SHA256",
                "target_app_key": "developer",
            }
        )
        params["sign"] = sign_params(self._credential.secret_key, params)
        return {key: str(value) for key, value in params.items()}
