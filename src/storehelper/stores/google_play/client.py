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

    @staticmethod
    def _version_visible(payload: Mapping[str, object], version_code: int) -> bool:
        raw_releases = payload.get("releases", [])
        if not isinstance(raw_releases, list):
            raise GooglePlayClient._protocol_error(
                "Google Play returned an invalid lifecycle release list."
            )
        for release in raw_releases:
            if not isinstance(release, Mapping):
                raise GooglePlayClient._protocol_error(
                    "Google Play returned an invalid lifecycle release."
                )
            raw_artifacts = release.get("activeArtifacts", [])
            if not isinstance(raw_artifacts, list):
                raise GooglePlayClient._protocol_error(
                    "Google Play returned invalid lifecycle artifacts."
                )
            for artifact in raw_artifacts:
                raw_version = artifact.get("versionCode") if isinstance(artifact, Mapping) else None
                if (
                    not isinstance(raw_version, int)
                    or isinstance(raw_version, bool)
                    or raw_version <= 0
                ):
                    raise GooglePlayClient._protocol_error(
                        "Google Play returned an invalid lifecycle version code."
                    )
                if raw_version == version_code:
                    return True
        return False

    async def is_version_visible(
        self,
        *,
        package_name: str,
        track: str,
        version_code: int,
    ) -> bool:
        payload = await self.list_releases(package_name=package_name, track=track)
        return self._version_visible(payload, version_code)

    async def reconcile_version(
        self,
        *,
        package_name: str,
        track: str,
        version_code: int,
        attempts: int = 3,
    ) -> bool:
        for attempt in range(attempts):
            if await self.is_version_visible(
                package_name=package_name,
                track=track,
                version_code=version_code,
            ):
                return True
            if attempt + 1 < attempts:
                await self._sleeper(float(attempt + 1))
        return False

    async def create_edit(self, *, package_name: str) -> str:
        package_segment = quote(package_name, safe="")
        payload = await self.request_json(
            "POST",
            f"/androidpublisher/v3/applications/{package_segment}/edits",
            json={},
        )
        return self._validate_edit(payload)

    def _validate_edit(
        self,
        payload: Mapping[str, object],
        *,
        expected_id: str | None = None,
    ) -> str:
        edit_id = payload.get("id")
        expiry = payload.get("expiryTimeSeconds")
        try:
            expiry_seconds = int(str(expiry))
        except (TypeError, ValueError):
            expiry_seconds = 0
        if (
            not isinstance(edit_id, str)
            or not edit_id
            or (expected_id is not None and edit_id != expected_id)
            or expiry_seconds <= 0
        ):
            raise self._protocol_error("Google Play returned an invalid App Edit.")
        return edit_id

    async def get_edit(self, *, package_name: str, edit_id: str) -> Mapping[str, object]:
        package_segment = quote(package_name, safe="")
        edit_segment = quote(edit_id, safe="")
        payload = await self.request_json(
            "GET",
            f"/androidpublisher/v3/applications/{package_segment}/edits/{edit_segment}",
        )
        self._validate_edit(payload, expected_id=edit_id)
        return payload

    async def validate_edit(self, *, package_name: str, edit_id: str) -> None:
        package_segment = quote(package_name, safe="")
        edit_segment = quote(edit_id, safe="")
        payload = await self.request_json(
            "POST",
            f"/androidpublisher/v3/applications/{package_segment}/edits/{edit_segment}:validate",
        )
        self._validate_edit(payload, expected_id=edit_id)

    async def commit_edit(self, *, package_name: str, edit_id: str) -> None:
        package_segment = quote(package_name, safe="")
        edit_segment = quote(edit_id, safe="")
        payload = await self.request_json(
            "POST",
            f"/androidpublisher/v3/applications/{package_segment}/edits/{edit_segment}:commit",
            retry_transient=False,
            params={
                "changesNotSentForReview": "false",
                "changesInReviewBehavior": "ERROR_IF_IN_REVIEW",
            },
        )
        self._validate_edit(payload, expected_id=edit_id)

    @staticmethod
    def _validate_track_payload(
        payload: Mapping[str, object],
        *,
        expected_track: str,
    ) -> list[dict[str, object]]:
        if payload.get("track") != expected_track:
            raise GooglePlayClient._protocol_error(
                "Google Play returned a different track identifier."
            )
        raw_releases = payload.get("releases", [])
        if not isinstance(raw_releases, list):
            raise GooglePlayClient._protocol_error("Google Play returned an invalid edit track.")
        releases: list[dict[str, object]] = []
        for raw_release in raw_releases:
            if not isinstance(raw_release, Mapping):
                raise GooglePlayClient._protocol_error(
                    "Google Play returned an invalid edit release."
                )
            status = raw_release.get("status")
            version_codes = raw_release.get("versionCodes")
            if (
                not isinstance(status, str)
                or not isinstance(version_codes, list)
                or not version_codes
                or not all(
                    isinstance(value, str) and value.isdigit() and int(value) > 0
                    for value in version_codes
                )
            ):
                raise GooglePlayClient._protocol_error(
                    "Google Play returned an invalid edit release."
                )
            releases.append(dict(raw_release))
        return releases

    async def get_track(
        self,
        *,
        package_name: str,
        edit_id: str,
        track: str,
    ) -> list[dict[str, object]]:
        package_segment = quote(package_name, safe="")
        edit_segment = quote(edit_id, safe="")
        track_segment = quote(track, safe="")
        payload = await self.request_json(
            "GET",
            f"/androidpublisher/v3/applications/{package_segment}/edits/"
            f"{edit_segment}/tracks/{track_segment}",
        )
        return self._validate_track_payload(payload, expected_track=track)

    async def update_track(
        self,
        *,
        package_name: str,
        edit_id: str,
        track: str,
        releases: list[dict[str, object]],
        expected_version_code: str,
    ) -> None:
        package_segment = quote(package_name, safe="")
        edit_segment = quote(edit_id, safe="")
        track_segment = quote(track, safe="")
        payload = await self.request_json(
            "PUT",
            f"/androidpublisher/v3/applications/{package_segment}/edits/"
            f"{edit_segment}/tracks/{track_segment}",
            json={"track": track, "releases": releases},
        )
        updated = self._validate_track_payload(payload, expected_track=track)
        confirmed = False
        for release in updated:
            version_codes = release.get("versionCodes")
            if isinstance(version_codes, list) and expected_version_code in version_codes:
                confirmed = True
                break
        if not confirmed:
            raise self._protocol_error(
                "Google Play track update did not confirm the uploaded version."
            )

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
