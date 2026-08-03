"""Safe asynchronous App Store Connect JSON:API client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import quote

import httpx

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.apple.auth import AppleAuth
from storehelper.stores.apple.errors import AppleVendorError, parse_apple_errors
from storehelper.stores.models import StoreTarget, VerifiedApplication

DEFAULT_API_BASE = "https://api.appstoreconnect.apple.com"
Sleeper = Callable[[float], Awaitable[None]]


class AppleClient:
    """Own authenticated JSON:API requests and verified target metadata."""

    def __init__(
        self,
        *,
        auth: AppleAuth,
        http: httpx.AsyncClient,
        api_base: str = DEFAULT_API_BASE,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._auth = auth
        self._http = http
        self._api_base = api_base.rstrip("/")
        self._sleeper = sleeper
        self._verified_versions: dict[str, str] = {}

    @staticmethod
    def _protocol_error(message: str) -> AppleVendorError:
        return AppleVendorError(
            "APPLE_RESPONSE_INVALID",
            message,
            ExitCode.VENDOR_REJECTION,
        )

    @staticmethod
    def _network_error(code: str, message: str) -> AppleVendorError:
        return AppleVendorError(code, message, ExitCode.NETWORK)

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
        if not path.startswith("/v1/") or "://" in path:
            raise self._protocol_error("App Store Connect request path is invalid.")
        url = f"{self._api_base}{path}"
        base_headers = dict(kwargs.pop("headers", {}))
        refreshed = False
        force_refresh = False
        transient_attempts = 0
        while True:
            headers = dict(base_headers)
            headers.update(self._auth.headers(force_refresh=force_refresh))
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
                    "APPLE_NETWORK_ERROR",
                    "Could not reach App Store Connect.",
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
                "APPLE_REDIRECT",
                "App Store Connect returned an unexpected redirect.",
            )
        try:
            value = response.json()
        except (ValueError, UnicodeDecodeError):
            if response.is_error:
                raise parse_apple_errors(None, status_code=response.status_code) from None
            raise self._protocol_error("App Store Connect returned a non-JSON response.") from None
        if response.is_error:
            raise parse_apple_errors(value, status_code=response.status_code)
        if not isinstance(value, Mapping):
            raise self._protocol_error("App Store Connect returned an invalid JSON response.")
        return value

    @staticmethod
    def _resource(
        payload: Mapping[str, object],
        *,
        resource_type: str,
        resource_id: str,
    ) -> Mapping[str, object]:
        data = payload.get("data")
        if (
            not isinstance(data, Mapping)
            or data.get("type") != resource_type
            or data.get("id") != resource_id
        ):
            raise AppleClient._protocol_error(
                f"App Store Connect returned an invalid {resource_type} resource."
            )
        return data

    async def verify_target(self, *, target: StoreTarget) -> VerifiedApplication:
        if target.release_id is None or target.platform != "IOS":
            raise AppleVendorError(
                "APPLE_TARGET_INVALID",
                "Apple target requires an iOS App Store version ID.",
                ExitCode.VENDOR_REJECTION,
            )
        app_id = quote(target.app_id, safe="")
        app_payload = await self.request_json(
            "GET",
            f"/v1/apps/{app_id}",
            params={"fields[apps]": "bundleId,name"},
        )
        app = self._resource(app_payload, resource_type="apps", resource_id=target.app_id)
        app_attributes = app.get("attributes")
        if not isinstance(app_attributes, Mapping):
            raise self._protocol_error("App Store Connect app attributes are missing.")
        if app_attributes.get("bundleId") != target.package_name:
            raise AppleVendorError(
                "APPLE_BUNDLE_MISMATCH",
                "The configured Apple bundle ID does not match the App Store Connect app.",
                ExitCode.VENDOR_REJECTION,
            )

        release_id = quote(target.release_id, safe="")
        version_payload = await self.request_json(
            "GET",
            f"/v1/appStoreVersions/{release_id}",
            params={
                "fields[appStoreVersions]": "platform,versionString,appStoreState",
                "include": "app",
            },
        )
        version = self._resource(
            version_payload,
            resource_type="appStoreVersions",
            resource_id=target.release_id,
        )
        attributes = version.get("attributes")
        if not isinstance(attributes, Mapping):
            raise self._protocol_error("App Store version attributes are missing.")
        if attributes.get("platform") != target.platform:
            raise AppleVendorError(
                "APPLE_PLATFORM_MISMATCH",
                "The configured Apple platform does not match the App Store version.",
                ExitCode.VENDOR_REJECTION,
            )
        version_string = attributes.get("versionString")
        if not isinstance(version_string, str) or not version_string.strip():
            raise self._protocol_error("App Store version string is missing.")
        relationships = version.get("relationships")
        app_relationship = relationships.get("app") if isinstance(relationships, Mapping) else None
        related_data = (
            app_relationship.get("data") if isinstance(app_relationship, Mapping) else None
        )
        if (
            not isinstance(related_data, Mapping)
            or related_data.get("type") != "apps"
            or related_data.get("id") != target.app_id
        ):
            raise AppleVendorError(
                "APPLE_VERSION_APP_MISMATCH",
                "The configured App Store version does not belong to the configured app.",
                ExitCode.VENDOR_REJECTION,
            )
        self._verified_versions[target.release_id] = version_string.strip()
        return VerifiedApplication(app_id=target.app_id, package_name=target.package_name)

    def verified_version_string(self, release_id: str) -> str | None:
        return self._verified_versions.get(release_id)
