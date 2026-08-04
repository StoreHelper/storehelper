"""Safe fixed-host HONOR Publish API client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.honor.auth import HonorAuth
from storehelper.stores.honor.errors import HonorVendorError, parse_honor_error
from storehelper.stores.honor.models import (
    HonorApplicationInfo,
    HonorCurrentRelease,
    HonorFileInfo,
    HonorLocaleInfo,
)

HONOR_API_BASE = "https://appmarket-openapi-drcn.cloud.honor.com"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
Sleeper = Callable[[float], Awaitable[None]]

_ALLOWED_PATHS = frozenset(
    {
        "/openapi/v1/publish/get-app-id",
        "/openapi/v1/publish/get-app-detail",
        "/openapi/v1/publish/get-app-current-release",
    }
)


class HonorClient:
    """Own the fixed HONOR API origin, authentication, and bounded retry policy."""

    def __init__(
        self,
        *,
        auth: HonorAuth,
        http: httpx.AsyncClient,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._auth = auth
        self._http = http
        self._sleeper = sleeper

    def __repr__(self) -> str:
        return "HonorClient(configured=True)"

    @staticmethod
    def _protocol_error(message: str) -> HonorVendorError:
        return HonorVendorError("HONOR_RESPONSE_INVALID", message, ExitCode.VENDOR_REJECTION)

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        raw = response.headers.get("retry-after") if response is not None else None
        if raw is not None:
            try:
                return min(60.0, max(0.0, float(raw)))
            except ValueError:
                pass
        return float(attempt)

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        retry_transient: bool,
        resumable_on_error: bool = False,
        **kwargs: Any,
    ) -> object:
        if path not in _ALLOWED_PATHS:
            raise self._protocol_error("HONOR request path is invalid.")
        url = f"{HONOR_API_BASE}{path}"
        base_headers = dict(kwargs.pop("headers", {}))
        refreshed = False
        force_refresh = False
        attempts = 0
        while True:
            headers = dict(base_headers)
            headers.update(await self._auth.headers(force_refresh=force_refresh))
            force_refresh = False
            response: httpx.Response | None = None
            try:
                response = await self._http.request(
                    method,
                    url,
                    headers=headers,
                    follow_redirects=False,
                    **kwargs,
                )
            except (OSError, httpx.HTTPError):
                if retry_transient and attempts < 2:
                    attempts += 1
                    await self._sleeper(self._retry_delay(None, attempts))
                    continue
                raise HonorVendorError(
                    "HONOR_NETWORK_ERROR",
                    "Could not reach the HONOR Publish API.",
                    ExitCode.NETWORK,
                    resumable=resumable_on_error,
                ) from None
            if response.status_code == 401 and not refreshed:
                refreshed = True
                force_refresh = True
                continue
            if (
                retry_transient
                and (response.status_code == 429 or response.status_code >= 500)
                and attempts < 2
            ):
                attempts += 1
                await self._sleeper(self._retry_delay(response, attempts))
                continue
            if response.is_redirect:
                raise HonorVendorError(
                    "HONOR_REDIRECT",
                    "HONOR returned an unexpected redirect.",
                    ExitCode.NETWORK,
                    status_code=response.status_code,
                    resumable=resumable_on_error,
                )
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise self._protocol_error("HONOR returned an oversized JSON response.")
            try:
                payload = response.json()
            except (ValueError, UnicodeDecodeError):
                if response.is_error:
                    raise parse_honor_error(
                        vendor_code=None,
                        status_code=response.status_code,
                        resumable=resumable_on_error,
                    ) from None
                raise self._protocol_error("HONOR returned a non-JSON response.") from None
            if not isinstance(payload, Mapping):
                raise self._protocol_error("HONOR returned an invalid JSON object.")
            code = payload.get("code")
            if response.is_error or code != 0:
                raise parse_honor_error(
                    vendor_code=code,
                    status_code=response.status_code,
                    vendor_message=payload.get("msg"),
                    resumable=resumable_on_error,
                )
            return payload.get("data")

    async def get_app_id(self, *, package_name: str) -> int:
        data = await self.request_json(
            "GET",
            "/openapi/v1/publish/get-app-id",
            retry_transient=True,
            params={"pkgName": package_name},
        )
        if not isinstance(data, list):
            raise self._protocol_error("HONOR returned an invalid application ID list.")
        matches: list[int] = []
        for value in data:
            if not isinstance(value, Mapping):
                raise self._protocol_error("HONOR returned an invalid application identity.")
            app_id = value.get("appId")
            returned_package = value.get("packageName")
            if returned_package == package_name:
                if not isinstance(app_id, int) or isinstance(app_id, bool) or app_id <= 0:
                    raise self._protocol_error("HONOR returned an invalid APPID.")
                matches.append(app_id)
        if not matches:
            raise HonorVendorError(
                "HONOR_APP_NOT_FOUND",
                "The configured package is not available to the HONOR API account.",
                ExitCode.VENDOR_REJECTION,
            )
        if len(matches) != 1:
            raise HonorVendorError(
                "HONOR_APP_ID_AMBIGUOUS",
                "HONOR returned more than one APPID for the configured package.",
                ExitCode.VENDOR_REJECTION,
            )
        return matches[0]

    @staticmethod
    def _optional_text(value: object, *, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise HonorClient._protocol_error(f"HONOR returned an invalid {field} value.")
        return value

    @classmethod
    def _parse_detail(cls, data: object, *, expected_app_id: int) -> HonorApplicationInfo:
        if not isinstance(data, Mapping):
            raise cls._protocol_error("HONOR returned invalid application detail.")
        basic = data.get("basicInfo")
        release = data.get("releaseInfo")
        raw_locales = data.get("languageInfo")
        raw_files = data.get("fileInfo", [])
        if (
            not isinstance(basic, Mapping)
            or not isinstance(release, Mapping)
            or not isinstance(raw_locales, list)
            or not isinstance(raw_files, list)
        ):
            raise cls._protocol_error("HONOR application detail is missing required sections.")
        app_id = basic.get("appId")
        package_name = basic.get("packageName")
        version_code = release.get("versionCode")
        if (
            app_id != expected_app_id
            or not isinstance(package_name, str)
            or not package_name
            or not isinstance(version_code, int)
            or isinstance(version_code, bool)
            or version_code <= 0
        ):
            raise cls._protocol_error("HONOR application identity or published version is invalid.")
        locales: list[HonorLocaleInfo] = []
        for raw in raw_locales:
            if not isinstance(raw, Mapping):
                raise cls._protocol_error("HONOR returned invalid localized application data.")
            language_id = raw.get("languageId")
            app_name = raw.get("appName")
            intro = raw.get("intro")
            if not all(
                isinstance(value, str) and value for value in (language_id, app_name, intro)
            ):
                raise cls._protocol_error("HONOR returned incomplete localized application data.")
            assert isinstance(language_id, str)
            assert isinstance(app_name, str)
            assert isinstance(intro, str)
            locales.append(
                HonorLocaleInfo(
                    language_id=language_id,
                    app_name=app_name,
                    intro=intro,
                    brief_intro=cls._optional_text(raw.get("briefIntro"), field="briefIntro"),
                    new_feature=cls._optional_text(raw.get("newFeature"), field="newFeature"),
                )
            )
        files: list[HonorFileInfo] = []
        for raw in raw_files:
            if not isinstance(raw, Mapping):
                raise cls._protocol_error("HONOR returned invalid application file data.")
            file_type = raw.get("fileType")
            sha256 = raw.get("fileSha256")
            if not isinstance(file_type, int) or isinstance(file_type, bool) or file_type <= 0:
                raise cls._protocol_error("HONOR returned an invalid application file type.")
            if sha256 is not None:
                if not isinstance(sha256, str):
                    raise cls._protocol_error("HONOR returned an invalid application file digest.")
                sha256 = sha256.lower()
            try:
                files.append(HonorFileInfo(file_type=file_type, sha256=sha256))
            except ValueError:
                raise cls._protocol_error(
                    "HONOR returned an invalid application file digest."
                ) from None
        try:
            return HonorApplicationInfo(
                app_id=app_id,
                package_name=package_name,
                published_version_code=version_code,
                locales=tuple(locales),
                files=tuple(files),
            )
        except ValueError:
            raise cls._protocol_error("HONOR returned incomplete application detail.") from None

    async def get_app_detail(self, *, app_id: int) -> HonorApplicationInfo:
        data = await self.request_json(
            "GET",
            "/openapi/v1/publish/get-app-detail",
            retry_transient=True,
            params={"appId": str(app_id)},
        )
        return self._parse_detail(data, expected_app_id=app_id)

    @classmethod
    def _parse_release(cls, data: object, *, expected_app_id: int) -> HonorCurrentRelease | None:
        if data is None:
            return None
        if not isinstance(data, Mapping):
            raise cls._protocol_error("HONOR returned invalid current release data.")
        app_id = data.get("appId")
        release_id = data.get("releaseId")
        version_code = data.get("versionCode")
        audit_result = data.get("auditResult")
        if (
            app_id != expected_app_id
            or (release_id is not None and (not isinstance(release_id, str) or not release_id))
            or (
                version_code is not None
                and (
                    not isinstance(version_code, int)
                    or isinstance(version_code, bool)
                    or version_code <= 0
                )
            )
            or not isinstance(audit_result, int)
            or isinstance(audit_result, bool)
            or audit_result < 0
        ):
            raise cls._protocol_error("HONOR returned an invalid current release identity.")
        return HonorCurrentRelease(
            app_id=app_id,
            release_id=release_id,
            version_code=version_code,
            audit_result=audit_result,
        )

    async def get_current_release(self, *, app_id: int) -> HonorCurrentRelease | None:
        data = await self.request_json(
            "GET",
            "/openapi/v1/publish/get-app-current-release",
            retry_transient=True,
            params={"appId": str(app_id)},
        )
        return self._parse_release(data, expected_app_id=app_id)
