"""Fixed-host asynchronous Xiaomi automatic-publishing API client."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping

import httpx

from storehelper.credentials.models import XiaomiApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.models import VerifiedApplication
from storehelper.stores.xiaomi.auth import XiaomiAuth
from storehelper.stores.xiaomi.errors import XiaomiVendorError, parse_xiaomi_error

DEFAULT_API_BASE = "https://api.developer.xiaomi.com/devupload"
Sleeper = Callable[[float], Awaitable[None]]


class XiaomiClient:
    """Own the only allowed Xiaomi API host and signed read-only query boundary."""

    def __init__(
        self,
        *,
        auth: XiaomiAuth,
        credential: XiaomiApiCredential,
        http: httpx.AsyncClient,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._auth = auth
        self._credential = credential
        self._http = http
        self._sleeper = sleeper

    @staticmethod
    def _protocol_error(message: str) -> XiaomiVendorError:
        return XiaomiVendorError(
            "XIAOMI_RESPONSE_INVALID",
            message,
            ExitCode.VENDOR_REJECTION,
        )

    @staticmethod
    def _network_error(code: str, message: str) -> XiaomiVendorError:
        return XiaomiVendorError(code, message, ExitCode.NETWORK)

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        raw = response.headers.get("retry-after") if response is not None else None
        if raw is not None:
            try:
                return min(60.0, max(0.0, float(raw)))
            except ValueError:
                pass
        return float(attempt)

    async def _query_response(self, request_data: str, signature: str) -> httpx.Response:
        url = f"{DEFAULT_API_BASE}/dev/query"
        attempt = 0
        while True:
            try:
                response = await self._http.post(
                    url,
                    files={
                        "RequestData": (None, request_data),
                        "SIG": (None, signature),
                    },
                    follow_redirects=False,
                )
            except (OSError, httpx.HTTPError):
                if attempt >= 2:
                    raise self._network_error(
                        "XIAOMI_NETWORK_ERROR",
                        "Could not reach the Xiaomi publishing API.",
                    ) from None
                attempt += 1
                await self._sleeper(self._retry_delay(None, attempt))
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                attempt += 1
                await self._sleeper(self._retry_delay(response, attempt))
                continue
            return response

    async def query_package(self, *, package_name: str) -> VerifiedApplication:
        request_data = json.dumps(
            {
                "packageName": package_name,
                "userName": self._credential.username,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        signature = self._auth.signature(request_data)
        response = await self._query_response(request_data, signature)
        if response.is_redirect:
            raise self._network_error(
                "XIAOMI_REDIRECT",
                "Xiaomi publishing returned an unexpected redirect.",
            )
        if response.is_error:
            raise parse_xiaomi_error(result=None, status_code=response.status_code)
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            raise self._protocol_error("Xiaomi publishing returned a non-JSON response.") from None
        if not isinstance(payload, Mapping):
            raise self._protocol_error("Xiaomi publishing returned an invalid JSON response.")
        result = payload.get("result")
        if not isinstance(result, int) or isinstance(result, bool):
            raise self._protocol_error("Xiaomi publishing response is missing an integer result.")
        if result != 0:
            raise parse_xiaomi_error(
                result=result,
                status_code=response.status_code,
                vendor_message=payload.get("message"),
            )
        package_info = payload.get("packageInfo")
        if package_info is None:
            raise XiaomiVendorError(
                "XIAOMI_PACKAGE_NOT_FOUND",
                "The configured package does not exist under this Xiaomi developer account.",
                ExitCode.VENDOR_REJECTION,
            )
        if not isinstance(package_info, Mapping):
            raise self._protocol_error("Xiaomi returned invalid package information.")
        returned_package = package_info.get("packageName")
        if not isinstance(returned_package, str) or returned_package != package_name:
            raise XiaomiVendorError(
                "XIAOMI_PACKAGE_MISMATCH",
                "Xiaomi returned a different package identity.",
                ExitCode.VENDOR_REJECTION,
            )
        update_allowed = payload.get("updateVersion")
        if not isinstance(update_allowed, bool):
            raise self._protocol_error("Xiaomi returned an invalid update permission.")
        if not update_allowed:
            raise XiaomiVendorError(
                "XIAOMI_UPDATE_NOT_ALLOWED",
                "Xiaomi does not currently allow a version update for this package.",
                ExitCode.VENDOR_REJECTION,
            )
        return VerifiedApplication(app_id=package_name, package_name=package_name)
