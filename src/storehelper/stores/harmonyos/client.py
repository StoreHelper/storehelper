"""Sanitized client for AppGallery Connect HarmonyOS Publishing API v2/v3."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from storehelper.artifacts.models import ArtifactInfo
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.harmonyos.models import OBSUploadTicket
from storehelper.stores.huawei.auth import HuaweiAuth
from storehelper.stores.huawei.errors import (
    HuaweiVendorError,
    parse_huawei_response,
    translate_huawei_error,
)
from storehelper.stores.models import UploadedArtifact, VerifiedApplication

DEFAULT_V2_API_BASE = "https://connect-api.cloud.huawei.com/api/publish/v2"
DEFAULT_V3_API_BASE = "https://connect-api.cloud.huawei.com/api/publish/v3"


class HarmonyOSClient:
    """Own authenticated v2/v3 JSON requests without exposing response bodies."""

    def __init__(
        self,
        *,
        auth: HuaweiAuth,
        http: httpx.AsyncClient,
        v2_api_base: str = DEFAULT_V2_API_BASE,
        v3_api_base: str = DEFAULT_V3_API_BASE,
    ) -> None:
        self._auth = auth
        self._http = http
        self._bases = {"v2": v2_api_base.rstrip("/"), "v3": v3_api_base.rstrip("/")}

    @staticmethod
    def _protocol_error(message: str) -> HuaweiVendorError:
        return HuaweiVendorError(
            "HARMONYOS_RESPONSE_INVALID",
            message,
            ExitCode.VENDOR_REJECTION,
        )

    @staticmethod
    def _network_error(message: str) -> HuaweiVendorError:
        return HuaweiVendorError("HARMONYOS_NETWORK_ERROR", message, ExitCode.NETWORK)

    async def _send_authenticated(
        self,
        version: Literal["v2", "v3"],
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        url = f"{self._bases[version]}/{path.lstrip('/')}"
        auth_refreshed = False
        refresh_token = False
        transient_attempts = 0
        while True:
            headers = dict(kwargs.pop("headers", {}))
            headers.update(self._auth.headers(force_refresh=refresh_token))
            refresh_token = False
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
                    "Could not reach the Huawei HarmonyOS Publishing API."
                ) from None
            if response.status_code in (401, 403) and not auth_refreshed:
                auth_refreshed = True
                refresh_token = True
                continue
            if (
                response.status_code == 429 or response.status_code >= 500
            ) and transient_attempts < 2:
                transient_attempts += 1
                continue
            return response

    async def request_json(
        self,
        version: Literal["v2", "v3"],
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Mapping[str, object]:
        response = await self._send_authenticated(version, method, path, **kwargs)
        if response.is_redirect:
            raise self._network_error("Huawei returned an unexpected redirect.")
        if response.status_code == 429 or response.status_code >= 500:
            raise self._network_error(
                "Huawei HarmonyOS Publishing API returned "
                f"HTTP {response.status_code} after retries."
            )
        try:
            value = response.json()
        except (ValueError, UnicodeDecodeError):
            raise self._protocol_error("Huawei returned a non-JSON response.") from None
        if not isinstance(value, Mapping):
            raise self._protocol_error("Huawei returned an invalid JSON response.")
        status = parse_huawei_response(value)
        if not status.success:
            raise translate_huawei_error(status.code, status.message or status.hint)
        if response.is_error:
            raise self._network_error(
                f"Huawei HarmonyOS Publishing API returned HTTP {response.status_code}."
            )
        return value

    async def verify_app(
        self,
        *,
        app_id: str,
        package_name: str,
    ) -> VerifiedApplication:
        data = await self.request_json(
            "v2",
            "GET",
            "appid-list",
            params={"packageName": package_name, "packageTypes": "7"},
        )
        raw_apps = data.get("appids")
        found = False
        if isinstance(raw_apps, list):
            for value in raw_apps:
                if isinstance(value, Mapping):
                    candidate = value.get("value") or value.get("appId")
                else:
                    candidate = value
                if str(candidate) == app_id:
                    found = True
                    break
        if not found:
            raise HuaweiVendorError(
                "HARMONYOS_APP_NOT_FOUND",
                "Huawei did not return the configured HarmonyOS app ID for this package name.",
                ExitCode.VENDOR_REJECTION,
            )
        return VerifiedApplication(app_id=app_id, package_name=package_name)

    async def request_obs_upload(
        self,
        *,
        app_id: str,
        artifact: ArtifactInfo,
    ) -> OBSUploadTicket:
        data = await self.request_json(
            "v2",
            "GET",
            "upload-url/for-obs",
            params={
                "appId": app_id,
                "fileName": artifact.logical_name,
                "contentLength": artifact.size,
                "releaseType": "1",
            },
        )
        raw = data.get("urlInfo")
        if not isinstance(raw, Mapping):
            raise self._protocol_error("Huawei OBS upload allocation is missing urlInfo.")
        url = raw.get("url")
        method = raw.get("method")
        object_id = raw.get("objectId")
        raw_headers = raw.get("headers")
        if not isinstance(url, str):
            raise self._protocol_error("Huawei returned an invalid OBS upload URL.")
        parsed = urlsplit(url)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise self._protocol_error("Huawei returned an unsafe OBS upload URL.")
        if not isinstance(method, str) or method.upper() != "PUT":
            raise self._protocol_error("Huawei returned an unsupported OBS upload method.")
        if not isinstance(object_id, str) or not object_id:
            raise self._protocol_error("Huawei OBS upload allocation is missing objectId.")
        if not isinstance(raw_headers, Mapping) or not raw_headers:
            raise self._protocol_error("Huawei OBS upload allocation is missing signed headers.")
        headers: dict[str, SecretStr] = {}
        for key, value in raw_headers.items():
            if not isinstance(key, str) or not key or not isinstance(value, str) or not value:
                raise self._protocol_error("Huawei returned invalid OBS signed headers.")
            headers[key] = SecretStr(value)
        return OBSUploadTicket(
            url=SecretStr(url),
            method="PUT",
            headers=headers,
            object_id=SecretStr(object_id),
        )

    async def upload_to_obs(
        self,
        *,
        artifact: ArtifactInfo,
        ticket: OBSUploadTicket,
    ) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            with artifact.path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    yield chunk

        headers = {key: value.get_secret_value() for key, value in ticket.headers.items()}
        try:
            response = await self._http.request(
                ticket.method,
                ticket.url.get_secret_value(),
                headers=headers,
                content=chunks(),
                follow_redirects=False,
            )
        except (OSError, httpx.HTTPError):
            raise HuaweiVendorError(
                "HARMONYOS_OBS_UPLOAD_FAILED",
                "Could not stream the HarmonyOS artifact to Huawei OBS.",
                ExitCode.NETWORK,
            ) from None
        if response.is_redirect or response.status_code not in (200, 204):
            raise HuaweiVendorError(
                "HARMONYOS_OBS_UPLOAD_FAILED",
                "Huawei OBS did not accept the HarmonyOS artifact.",
                ExitCode.NETWORK,
            )

    async def bind_package(
        self,
        *,
        app_id: str,
        artifact: ArtifactInfo,
        object_id: str,
    ) -> UploadedArtifact:
        data = await self.request_json(
            "v3",
            "PUT",
            "app-package-info",
            params={"appId": app_id, "releaseType": "1"},
            json={"fileName": artifact.logical_name, "objectId": object_id},
        )
        package_id = data.get("packageId")
        if not isinstance(package_id, str) or not package_id:
            raise self._protocol_error("Huawei package binding is missing packageId.")
        return UploadedArtifact(artifact_id=package_id)

    async def upload_and_bind(
        self,
        *,
        app_id: str,
        artifact: ArtifactInfo,
    ) -> UploadedArtifact:
        ticket = await self.request_obs_upload(app_id=app_id, artifact=artifact)
        await self.upload_to_obs(artifact=artifact, ticket=ticket)
        return await self.bind_package(
            app_id=app_id,
            artifact=artifact,
            object_id=ticket.object_id.get_secret_value(),
        )
