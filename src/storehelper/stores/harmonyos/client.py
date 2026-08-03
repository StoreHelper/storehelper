"""Sanitized client for AppGallery Connect HarmonyOS Publishing API v2/v3."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import httpx

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.huawei.auth import HuaweiAuth
from storehelper.stores.huawei.errors import (
    HuaweiVendorError,
    parse_huawei_response,
    translate_huawei_error,
)
from storehelper.stores.models import VerifiedApplication

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
