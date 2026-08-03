"""Fixed-host asynchronous OPPO Open Platform API client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping

import httpx
from pydantic import ValidationError

from storehelper.credentials.models import OppoApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.oppo.auth import OppoAuth
from storehelper.stores.oppo.errors import (
    OppoVendorError,
    is_oppo_auth_failure,
    parse_oppo_error,
)
from storehelper.stores.oppo.models import OppoApplicationInfo

DEFAULT_API_BASE = "https://oop-openapi-cn.heytapmobi.com"
_TOKEN_PATH = "/developer/v1/token"
_APPLICATION_INFO_PATH = "/resource/v1/app/info"
_ALLOWED_READ_PATHS = frozenset(
    {
        _TOKEN_PATH,
        _APPLICATION_INFO_PATH,
        "/resource/v1/upload/get-upload-url",
    }
)
Sleeper = Callable[[float], Awaitable[None]]


class OppoClient:
    """Own the fixed OPPO host and all secret-bearing HTTP exchanges."""

    def __init__(
        self,
        *,
        credential: OppoApiCredential,
        auth: OppoAuth,
        http: httpx.AsyncClient,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._credential = credential
        self._auth = auth
        self._http = http
        self._sleeper = sleeper

    @staticmethod
    def _protocol_error(message: str) -> OppoVendorError:
        return OppoVendorError(
            "OPPO_RESPONSE_INVALID",
            message,
            ExitCode.VENDOR_REJECTION,
        )

    @staticmethod
    def _network_error(code: str, message: str) -> OppoVendorError:
        return OppoVendorError(code, message, ExitCode.NETWORK)

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        raw = response.headers.get("retry-after") if response is not None else None
        if raw is not None:
            try:
                return min(60.0, max(0.0, float(raw)))
            except ValueError:
                pass
        return float(attempt)

    async def _get(self, path: str, *, params: Mapping[str, str]) -> httpx.Response:
        if path not in _ALLOWED_READ_PATHS:
            raise self._protocol_error("OPPO request path is invalid.")
        attempt = 0
        while True:
            try:
                response = await self._http.get(
                    f"{DEFAULT_API_BASE}{path}",
                    params=params,
                    follow_redirects=False,
                )
            except (OSError, httpx.HTTPError):
                if attempt >= 2:
                    raise self._network_error(
                        "OPPO_NETWORK_ERROR",
                        "Could not reach the OPPO publishing API.",
                    ) from None
                attempt += 1
                await self._sleeper(self._retry_delay(None, attempt))
                continue
            if response.is_redirect:
                raise self._network_error(
                    "OPPO_REDIRECT",
                    "OPPO publishing returned an unexpected redirect.",
                )
            if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                attempt += 1
                await self._sleeper(self._retry_delay(response, attempt))
                continue
            return response

    def _response_payload(self, response: httpx.Response) -> tuple[Mapping[str, object], int]:
        if response.is_error:
            raise parse_oppo_error(errno=None, status_code=response.status_code)
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            raise self._protocol_error("OPPO publishing returned a non-JSON response.") from None
        if not isinstance(payload, Mapping):
            raise self._protocol_error("OPPO publishing returned an invalid JSON response.")
        errno = payload.get("errno")
        if not isinstance(errno, int) or isinstance(errno, bool):
            raise self._protocol_error("OPPO publishing response is missing an integer errno.")
        return payload, errno

    async def ensure_token(self, *, force_refresh: bool = False) -> str:
        token = self._auth.cached_token(force_refresh=force_refresh)
        if token is not None:
            return token
        response = await self._get(
            _TOKEN_PATH,
            params={
                "client_id": self._credential.client_id.get_secret_value(),
                "client_secret": self._credential.client_secret.get_secret_value(),
            },
        )
        payload, errno = self._response_payload(response)
        if errno != 0:
            raise parse_oppo_error(
                errno=errno,
                status_code=response.status_code,
                vendor_message=payload.get("data"),
            )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise self._protocol_error("OPPO token response is incomplete.")
        access_token = data.get("access_token")
        if not isinstance(access_token, str):
            raise self._protocol_error("OPPO token response is incomplete.")
        try:
            self._auth.cache_token(access_token, expires_in=data.get("expire_in"))
        except ValueError:
            raise self._protocol_error("OPPO token response is incomplete.") from None
        cached = self._auth.cached_token()
        if cached is None:
            raise self._protocol_error("OPPO token response contains an expired token.")
        return cached

    async def _application_payload(self, package_name: str) -> Mapping[str, object]:
        refreshed = False
        while True:
            await self.ensure_token(force_refresh=refreshed)
            params = self._auth.signed_params({"pkg_name": package_name})
            response = await self._get(_APPLICATION_INFO_PATH, params=params)
            if response.status_code in {401, 403} and not refreshed:
                self._auth.clear_token()
                refreshed = True
                continue
            payload, errno = self._response_payload(response)
            if (
                is_oppo_auth_failure(errno=errno, status_code=response.status_code)
                and not refreshed
            ):
                self._auth.clear_token()
                refreshed = True
                continue
            if errno != 0:
                raise parse_oppo_error(
                    errno=errno,
                    status_code=response.status_code,
                    vendor_message=payload.get("data"),
                )
            data = payload.get("data")
            if not isinstance(data, Mapping):
                raise self._protocol_error("OPPO application response is incomplete.")
            return data

    @staticmethod
    def _positive_integer(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _nonnegative_integer(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _text(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return None

    async def application_info(self, package_name: str) -> OppoApplicationInfo:
        data = await self._application_payload(package_name)
        returned_package = data.get("pkg_name")
        if not isinstance(returned_package, str) or not returned_package.strip():
            raise OppoVendorError(
                "OPPO_APPLICATION_INCOMPLETE",
                "The existing OPPO application information is incomplete.",
                ExitCode.VENDOR_REJECTION,
            )
        if returned_package != package_name:
            raise OppoVendorError(
                "OPPO_PACKAGE_MISMATCH",
                "OPPO returned a different package identity.",
                ExitCode.VENDOR_REJECTION,
            )
        version_code = self._positive_integer(data.get("version_code"))
        audit_status = self._nonnegative_integer(data.get("audit_status"))
        if version_code is None or audit_status is None:
            raise OppoVendorError(
                "OPPO_APPLICATION_INFO_INVALID",
                "OPPO returned invalid application version or audit information.",
                ExitCode.VENDOR_REJECTION,
            )
        text_fields = (
            "app_name",
            "second_category_id",
            "third_category_id",
            "summary",
            "detail_desc",
            "privacy_source_url",
            "icon_url",
            "pic_url",
            "age_level",
            "adaptive_equipment",
            "copyright_url",
            "business_username",
            "business_email",
            "business_mobile",
        )
        normalized = {name: self._text(data.get(name)) for name in text_fields}
        if any(value is None for value in normalized.values()):
            raise OppoVendorError(
                "OPPO_APPLICATION_INCOMPLETE",
                "The existing OPPO application information is incomplete.",
                ExitCode.VENDOR_REJECTION,
            )
        try:
            return OppoApplicationInfo.model_validate(
                {
                    "package_name": returned_package,
                    "version_code": version_code,
                    "audit_status": audit_status,
                    **normalized,
                }
            )
        except ValidationError:
            raise OppoVendorError(
                "OPPO_APPLICATION_INCOMPLETE",
                "The existing OPPO application information is incomplete.",
                ExitCode.VENDOR_REJECTION,
            ) from None
