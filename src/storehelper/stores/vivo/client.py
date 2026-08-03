"""Fixed-host asynchronous vivo Open Platform API client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping

import httpx
from pydantic import SecretStr

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.vivo.auth import VivoAuth
from storehelper.stores.vivo.errors import VivoVendorError, parse_vivo_error
from storehelper.stores.vivo.models import VivoApplicationInfo, VivoUploadedApk
from storehelper.stores.vivo.package import VivoArtifactInfo

DEFAULT_API_URL = "https://developer-api.vivo.com.cn/router/rest"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_QUERY_METHOD = "app.query.details"
_ALLOWED_METHODS = frozenset(
    {
        _QUERY_METHOD,
        "app.upload.apk.app.64",
        "app.sync.update.app",
    }
)
Sleeper = Callable[[float], Awaitable[None]]


class VivoClient:
    """Own the fixed vivo gateway and all signed HTTP exchanges."""

    def __init__(
        self,
        *,
        auth: VivoAuth,
        http: httpx.AsyncClient,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._auth = auth
        self._http = http
        self._sleeper = sleeper

    @staticmethod
    def _protocol_error(code: str, message: str) -> VivoVendorError:
        return VivoVendorError(code, message, ExitCode.VENDOR_REJECTION)

    @staticmethod
    def _network_error(code: str, message: str) -> VivoVendorError:
        return VivoVendorError(code, message, ExitCode.NETWORK)

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        raw = response.headers.get("retry-after") if response is not None else None
        if raw is not None:
            try:
                return min(60.0, max(0.0, float(raw)))
            except ValueError:
                pass
        return float(attempt)

    async def _post(
        self,
        method: str,
        *,
        business: Mapping[str, object],
        retry_transient: bool,
    ) -> httpx.Response:
        if method not in _ALLOWED_METHODS:
            raise self._protocol_error("VIVO_METHOD_INVALID", "vivo request method is invalid.")
        attempt = 0
        while True:
            params = self._auth.signed_params(method, business)
            try:
                response = await self._http.post(
                    DEFAULT_API_URL,
                    data=params,
                    follow_redirects=False,
                )
            except (OSError, httpx.HTTPError):
                if not retry_transient or attempt >= 2:
                    raise self._network_error(
                        "VIVO_NETWORK_ERROR",
                        "Could not reach the vivo publishing API.",
                    ) from None
                attempt += 1
                await self._sleeper(self._retry_delay(None, attempt))
                continue
            if response.is_redirect:
                raise self._network_error(
                    "VIVO_REDIRECT",
                    "vivo publishing returned an unexpected redirect.",
                )
            if (
                retry_transient
                and (response.status_code == 429 or response.status_code >= 500)
                and attempt < 2
            ):
                attempt += 1
                await self._sleeper(self._retry_delay(response, attempt))
                continue
            return response

    def _response_payload(self, response: httpx.Response) -> Mapping[str, object]:
        if response.is_error:
            raise parse_vivo_error(code=None, sub_code=None, status_code=response.status_code)
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_RESPONSE_BYTES:
                    raise self._protocol_error(
                        "VIVO_RESPONSE_TOO_LARGE",
                        "vivo publishing returned an oversized response.",
                    )
            except ValueError:
                pass
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise self._protocol_error(
                "VIVO_RESPONSE_TOO_LARGE",
                "vivo publishing returned an oversized response.",
            )
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            raise self._protocol_error(
                "VIVO_RESPONSE_INVALID",
                "vivo publishing returned a non-JSON response.",
            ) from None
        if not isinstance(payload, Mapping):
            raise self._protocol_error(
                "VIVO_RESPONSE_INVALID",
                "vivo publishing returned an invalid JSON response.",
            )
        code_value = payload.get("code")
        sub_value = payload.get("subCode")
        code = str(code_value) if isinstance(code_value, (str, int)) else None
        sub_code = str(sub_value) if isinstance(sub_value, (str, int)) else None
        if code != "0" or sub_code not in {None, "0"}:
            raise parse_vivo_error(
                code=code,
                sub_code=sub_code,
                status_code=response.status_code,
                vendor_message=payload.get("msg"),
            )
        return payload

    @staticmethod
    def _positive_integer(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    async def application_info(self, package_name: str) -> VivoApplicationInfo:
        response = await self._post(
            _QUERY_METHOD,
            business={"packageName": package_name},
            retry_transient=True,
        )
        payload = self._response_payload(response)
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise self._protocol_error(
                "VIVO_APPLICATION_INFO_INVALID",
                "vivo returned incomplete application information.",
            )
        returned_package = data.get("packageName")
        if not isinstance(returned_package, str) or not returned_package.strip():
            raise self._protocol_error(
                "VIVO_APPLICATION_INFO_INVALID",
                "vivo returned incomplete application information.",
            )
        if returned_package != package_name:
            raise self._protocol_error(
                "VIVO_PACKAGE_MISMATCH",
                "vivo returned a different package identity.",
            )
        version_code = self._positive_integer(data.get("versionCode"))
        status = self._positive_integer(data.get("status"))
        if version_code is None or status not in {1, 2, 3, 4, 5, 6}:
            raise self._protocol_error(
                "VIVO_APPLICATION_INFO_INVALID",
                "vivo returned invalid application version or review information.",
            )
        return VivoApplicationInfo(
            package_name=returned_package,
            version_code=version_code,
            status=status,
        )

    async def upload_apk(
        self,
        *,
        package_name: str,
        artifact: VivoArtifactInfo,
    ) -> VivoUploadedApk:
        """Stream one APK through vivo's fixed multipart upload method exactly once."""

        params = self._auth.signed_params(
            "app.upload.apk.app.64",
            {"packageName": package_name, "fileMd5": artifact.md5},
        )
        try:
            with artifact.path.open("rb") as package:
                response = await self._http.post(
                    DEFAULT_API_URL,
                    data=params,
                    files={
                        "file": (
                            artifact.logical_name,
                            package,
                            "application/vnd.android.package-archive",
                        )
                    },
                    follow_redirects=False,
                )
        except OSError:
            raise VivoVendorError(
                "VIVO_LOCAL_FILE_ERROR",
                "The vivo APK became unreadable before upload.",
                ExitCode.PACKAGE_VALIDATION,
            ) from None
        except httpx.HTTPError:
            raise self._network_error(
                "VIVO_NETWORK_ERROR",
                "The vivo APK upload response was not received.",
            ) from None
        if response.is_redirect:
            raise self._network_error(
                "VIVO_REDIRECT",
                "vivo publishing returned an unexpected redirect.",
            )
        payload = self._response_payload(response)
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise self._protocol_error(
                "VIVO_UPLOAD_RESULT_INVALID",
                "vivo returned an incomplete APK upload result.",
            )
        serialnumber = data.get("serialnumber")
        returned_md5 = data.get("fileMd5", artifact.md5)
        if (
            not isinstance(serialnumber, str)
            or not serialnumber.strip()
            or len(serialnumber.strip()) > 512
            or not isinstance(returned_md5, str)
            or returned_md5.lower() != artifact.md5
        ):
            raise self._protocol_error(
                "VIVO_UPLOAD_RESULT_INVALID",
                "vivo returned an incomplete APK upload result.",
            )
        return VivoUploadedApk(
            serialnumber=SecretStr(serialnumber.strip()),
            md5=SecretStr(artifact.md5),
        )

    async def submit_update(
        self,
        *,
        package_name: str,
        version_code: int,
        uploaded: VivoUploadedApk,
        release_notes: str,
    ) -> str:
        """Perform the single non-retryable vivo review-submission mutation."""

        try:
            response = await self._post(
                "app.sync.update.app",
                business={
                    "packageName": package_name,
                    "versionCode": str(version_code),
                    "apk": uploaded.serialnumber.get_secret_value(),
                    "fileMd5": uploaded.md5.get_secret_value(),
                    "onlineType": "1",
                    "compatibleDevice": "1",
                    "updateDesc": release_notes,
                },
                retry_transient=False,
            )
        except VivoVendorError as error:
            if error.code == "VIVO_NETWORK_ERROR":
                raise VivoVendorError(
                    "VIVO_SUBMISSION_UNCERTAIN",
                    "The vivo final submission response was not received; "
                    "inspect the vivo console.",
                    ExitCode.NETWORK,
                ) from None
            raise
        self._response_payload(response)
        return package_name
