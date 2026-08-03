"""Safe asynchronous Google Play Developer API client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import quote

import httpx

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.google_play.auth import GoogleAuth
from storehelper.stores.google_play.errors import GoogleVendorError, parse_google_error

DEFAULT_API_BASE = "https://androidpublisher.googleapis.com"
Sleeper = Callable[[float], Awaitable[None]]


class GooglePlayClient:
    """Own the fixed-host authenticated Google Play HTTP boundary."""

    def __init__(
        self,
        *,
        auth: GoogleAuth,
        http: httpx.AsyncClient,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._auth = auth
        self._http = http
        self._sleeper = sleeper

    @staticmethod
    def _protocol_error(message: str) -> GoogleVendorError:
        return GoogleVendorError(
            "GOOGLE_RESPONSE_INVALID",
            message,
            ExitCode.VENDOR_REJECTION,
        )

    @staticmethod
    def _network_error(code: str, message: str) -> GoogleVendorError:
        return GoogleVendorError(code, message, ExitCode.NETWORK)

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("retry-after")
        if raw is not None:
            try:
                return min(60.0, max(0.0, float(raw)))
            except ValueError:
                pass
        return float(attempt)

    async def _send_authenticated(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        if not path.startswith("/androidpublisher/v3/") or "://" in path:
            raise self._protocol_error("Google Play request path is invalid.")
        url = f"{DEFAULT_API_BASE}{path}"
        base_headers = dict(kwargs.pop("headers", {}))
        refreshed = False
        force_refresh = False
        transient_attempts = 0
        while True:
            headers = dict(base_headers)
            headers.update(await self._auth.headers(force_refresh=force_refresh))
            force_refresh = False
            try:
                response = await self._http.request(
                    method,
                    url,
                    headers=headers,
                    follow_redirects=False,
                    **kwargs,
                )
            except httpx.HTTPError:
                raise self._network_error(
                    "GOOGLE_NETWORK_ERROR",
                    "Could not reach the Google Play Developer API.",
                ) from None
            if response.status_code == 401 and not refreshed:
                refreshed = True
                force_refresh = True
                continue
            if (
                response.status_code == 429 or response.status_code >= 500
            ) and transient_attempts < 2:
                transient_attempts += 1
                await self._sleeper(self._retry_delay(response, transient_attempts))
                continue
            return response

    async def request_json(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Mapping[str, object]:
        response = await self._send_authenticated(method, path, **kwargs)
        if response.is_redirect:
            raise self._network_error(
                "GOOGLE_REDIRECT",
                "Google Play returned an unexpected redirect.",
            )
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            if response.is_error:
                raise parse_google_error(None, status_code=response.status_code) from None
            raise self._protocol_error("Google Play returned a non-JSON response.") from None
        if response.is_error:
            raise parse_google_error(payload, status_code=response.status_code)
        if not isinstance(payload, Mapping):
            raise self._protocol_error("Google Play returned an invalid JSON response.")
        return payload

    async def list_releases(
        self,
        *,
        package_name: str,
        track: str,
    ) -> Mapping[str, object]:
        package_segment = quote(package_name, safe="")
        track_segment = quote(track, safe="")
        return await self.request_json(
            "GET",
            f"/androidpublisher/v3/applications/{package_segment}/tracks/{track_segment}/releases",
        )
