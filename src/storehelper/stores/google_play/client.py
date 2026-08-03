"""Safe asynchronous Google Play Developer API client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import quote

import httpx

from storehelper.artifacts.models import ArtifactInfo
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.google_play.auth import GoogleAuth
from storehelper.stores.google_play.errors import GoogleVendorError, parse_google_error

DEFAULT_API_BASE = "https://androidpublisher.googleapis.com"
Sleeper = Callable[[float], Awaitable[None]]
UPLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=600.0, pool=10.0)


class _ArtifactByteStream(httpx.AsyncByteStream):
    def __init__(self, artifact: ArtifactInfo) -> None:
        self._artifact = artifact

    async def __aiter__(self) -> AsyncIterator[bytes]:
        with self._artifact.path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                yield chunk


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
        *,
        retry_transient: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        allowed_prefixes = (
            "/androidpublisher/v3/",
            "/upload/androidpublisher/v3/",
        )
        if not path.startswith(allowed_prefixes) or "://" in path:
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
            except (OSError, httpx.HTTPError):
                raise self._network_error(
                    "GOOGLE_NETWORK_ERROR",
                    "Could not reach the Google Play Developer API.",
                ) from None
            if response.status_code == 401 and not refreshed:
                refreshed = True
                force_refresh = True
                continue
            if (
                (response.status_code == 429 or response.status_code >= 500)
                and retry_transient
                and transient_attempts < 2
            ):
                transient_attempts += 1
                await self._sleeper(self._retry_delay(response, transient_attempts))
                continue
            return response

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        retry_transient: bool = True,
        **kwargs: Any,
    ) -> Mapping[str, object]:
        response = await self._send_authenticated(
            method,
            path,
            retry_transient=retry_transient,
            **kwargs,
        )
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

    async def create_edit(self, *, package_name: str) -> str:
        package_segment = quote(package_name, safe="")
        payload = await self.request_json(
            "POST",
            f"/androidpublisher/v3/applications/{package_segment}/edits",
            json={},
        )
        edit_id = payload.get("id")
        expiry = payload.get("expiryTimeSeconds")
        try:
            expiry_seconds = int(str(expiry))
        except (TypeError, ValueError):
            expiry_seconds = 0
        if not isinstance(edit_id, str) or not edit_id or expiry_seconds <= 0:
            raise self._protocol_error("Google Play returned an invalid App Edit.")
        return edit_id

    async def upload_artifact(
        self,
        *,
        package_name: str,
        edit_id: str,
        artifact: ArtifactInfo,
    ) -> int:
        endpoint = {"aab": "bundles", "apk": "apks"}.get(str(artifact.kind))
        if endpoint is None:
            raise self._protocol_error("Google Play upload requires an AAB or APK artifact.")
        package_segment = quote(package_name, safe="")
        edit_segment = quote(edit_id, safe="")
        payload = await self.request_json(
            "POST",
            f"/upload/androidpublisher/v3/applications/{package_segment}/edits/"
            f"{edit_segment}/{endpoint}",
            retry_transient=False,
            params={"uploadType": "media"},
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(artifact.size),
            },
            content=_ArtifactByteStream(artifact),
            timeout=UPLOAD_TIMEOUT,
        )
        version_code = payload.get("versionCode")
        if endpoint == "bundles":
            sha256 = payload.get("sha256")
        else:
            binary = payload.get("binary")
            sha256 = binary.get("sha256") if isinstance(binary, Mapping) else None
        if (
            not isinstance(version_code, int)
            or isinstance(version_code, bool)
            or version_code <= 0
            or not isinstance(sha256, str)
            or sha256 != artifact.sha256
        ):
            raise self._protocol_error(
                "Google Play returned an invalid version code or SHA-256 digest."
            )
        return version_code
