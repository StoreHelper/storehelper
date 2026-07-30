"""Sanitized asynchronous client for Huawei Android Publishing API v2."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import SecretStr

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.huawei.auth import HuaweiAuth
from storehelper.stores.huawei.errors import (
    HuaweiVendorError,
    parse_huawei_response,
    translate_huawei_error,
)
from storehelper.stores.huawei.models import (
    BoundPackage,
    HuaweiApp,
    UploadedFile,
    UploadTicket,
)
from storehelper.stores.huawei.package import PackageInfo

DEFAULT_API_BASE = "https://connect-api.cloud.huawei.com/api/publish/v2"


class HuaweiClient:
    """Own the HTTP boundary while keeping headers and response bodies private."""

    def __init__(
        self,
        *,
        auth: HuaweiAuth,
        http: httpx.AsyncClient,
        api_base: str = DEFAULT_API_BASE,
    ) -> None:
        self._auth = auth
        self._http = http
        self._api_base = api_base.rstrip("/")

    @staticmethod
    def _protocol_error(message: str) -> HuaweiVendorError:
        return HuaweiVendorError(
            "HUAWEI_RESPONSE_INVALID",
            message,
            ExitCode.VENDOR_REJECTION,
        )

    @staticmethod
    def _network_error(message: str) -> HuaweiVendorError:
        return HuaweiVendorError("HUAWEI_NETWORK_ERROR", message, ExitCode.NETWORK)

    async def _send_authenticated(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        url = f"{self._api_base}/{path.lstrip('/')}"
        auth_refreshed = False
        transient_attempts = 0
        while True:
            headers = dict(kwargs.pop("headers", {}))
            headers.update(self._auth.headers(force_refresh=auth_refreshed))
            try:
                response = await self._http.request(
                    method,
                    url,
                    headers=headers,
                    follow_redirects=False,
                    **kwargs,
                )
            except httpx.HTTPError:
                raise self._network_error("Could not reach the Huawei Publishing API.") from None
            if response.status_code in (401, 403) and not auth_refreshed:
                auth_refreshed = True
                continue
            if (
                response.status_code == 429 or response.status_code >= 500
            ) and transient_attempts < 2:
                transient_attempts += 1
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
            raise self._network_error("Huawei returned an unexpected redirect.")
        if response.status_code == 429 or response.status_code >= 500:
            raise self._network_error(
                f"Huawei Publishing API returned HTTP {response.status_code} after retries."
            )
        data = self._decode_json(response)
        status = parse_huawei_response(data)
        if not status.success:
            raise translate_huawei_error(status.code, status.message or status.hint)
        if response.is_error:
            raise self._network_error(
                f"Huawei Publishing API returned HTTP {response.status_code}."
            )
        return data

    def _decode_json(self, response: httpx.Response) -> Mapping[str, object]:
        try:
            value = response.json()
        except (ValueError, UnicodeDecodeError):
            raise self._protocol_error("Huawei returned a non-JSON response.") from None
        if not isinstance(value, Mapping):
            raise self._protocol_error("Huawei returned an invalid JSON response.")
        return value

    async def verify_app(self, *, app_id: str, package_name: str) -> HuaweiApp:
        data = await self.request_json(
            "GET",
            "appid-list",
            params={"packageName": package_name, "packageTypes": "1"},
        )
        raw_apps = data.get("appids")
        if not isinstance(raw_apps, list):
            raw_apps = data.get("apps")
        found = False
        if isinstance(raw_apps, list):
            for value in raw_apps:
                candidate = value.get("appId") if isinstance(value, Mapping) else value
                if str(candidate) == app_id:
                    found = True
                    break
        if not found:
            raise HuaweiVendorError(
                "HUAWEI_APP_NOT_FOUND",
                "Huawei did not return the configured app ID for this package name.",
                ExitCode.VENDOR_REJECTION,
            )
        return HuaweiApp(app_id=app_id, package_name=package_name)

    async def request_upload(self, *, app_id: str, suffix: str) -> UploadTicket:
        data = await self.request_json(
            "GET",
            "upload-url",
            params={"appId": app_id, "suffix": suffix.lstrip(".")},
        )
        result = data.get("result")
        if not isinstance(result, Mapping):
            raise self._protocol_error("Huawei upload authorization is missing its result.")
        upload_url = result.get("uploadUrl")
        auth_code = result.get("authCode")
        if not isinstance(upload_url, str) or not upload_url.startswith("https://"):
            raise self._protocol_error("Huawei returned an invalid HTTPS upload URL.")
        if not isinstance(auth_code, str) or not auth_code:
            raise self._protocol_error("Huawei upload authorization is missing authCode.")
        server_name = result.get("fileName")
        return UploadTicket(
            upload_url=SecretStr(upload_url),
            auth_code=SecretStr(auth_code),
            server_file_name=server_name if isinstance(server_name, str) else None,
        )

    async def upload_file(
        self,
        *,
        package: PackageInfo,
        upload_url: str,
        auth_code: str,
    ) -> UploadedFile:
        if not upload_url.startswith("https://"):
            raise self._network_error("Huawei upload URL must use HTTPS.")
        try:
            with package.path.open("rb") as binary:
                response = await self._http.post(
                    upload_url,
                    data={"authCode": auth_code, "fileCount": "1"},
                    files={
                        "file": (
                            package.logical_name,
                            binary,
                            "application/octet-stream",
                        )
                    },
                    follow_redirects=False,
                )
        except (OSError, httpx.HTTPError):
            raise self._network_error(
                "Could not upload the package to Huawei FileServer."
            ) from None
        if response.is_redirect:
            raise self._network_error(
                "Huawei FileServer returned a redirect; upload credentials were not forwarded."
            )
        if response.is_error:
            raise self._network_error(f"Huawei FileServer returned HTTP {response.status_code}.")
        data = self._decode_json(response)
        if "ret" in data:
            status = parse_huawei_response(data)
            if not status.success:
                raise translate_huawei_error(status.code, status.message or status.hint)
        upload_rsp = data.get("result")
        if isinstance(upload_rsp, Mapping):
            upload_rsp = upload_rsp.get("UploadFileRsp", upload_rsp)
        if not isinstance(upload_rsp, Mapping):
            raise self._protocol_error("Huawei FileServer response is missing UploadFileRsp.")
        if str(upload_rsp.get("ifSuccess")) not in ("1", "True", "true"):
            raise self._protocol_error("Huawei FileServer did not confirm a successful upload.")
        files = upload_rsp.get("fileInfoList")
        if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], Mapping):
            raise self._protocol_error("Huawei FileServer must return exactly one uploaded file.")
        destination = files[0].get("fileDestUrl") or files[0].get("fileDestUlr")
        if not isinstance(destination, str) or not destination.startswith("https://"):
            raise self._protocol_error("Huawei FileServer returned an invalid destination.")
        return UploadedFile(destination=SecretStr(destination))

    async def bind_package(
        self,
        *,
        app_id: str,
        package: PackageInfo,
        destination: str,
    ) -> BoundPackage:
        data = await self.request_json(
            "PUT",
            "app-file-info",
            params={"appId": app_id, "releaseType": "1"},
            json={
                "fileType": 5,
                "files": [
                    {
                        "fileName": package.logical_name,
                        "fileDestUrl": destination,
                    }
                ],
            },
        )
        raw_version = data.get("pkgVersion")
        versions = raw_version if isinstance(raw_version, list) else [raw_version]
        values = [str(value) for value in versions if value is not None and str(value)]
        if len(values) != 1:
            raise self._protocol_error("Huawei binding must return exactly one pkgVersion.")
        return BoundPackage(pkg_version=values[0])

    async def upload_and_bind(
        self,
        *,
        app_id: str,
        package: PackageInfo,
    ) -> BoundPackage:
        ticket = await self.request_upload(app_id=app_id, suffix=package.kind.value)
        uploaded = await self.upload_file(
            package=package,
            upload_url=ticket.upload_url.get_secret_value(),
            auth_code=ticket.auth_code.get_secret_value(),
        )
        return await self.bind_package(
            app_id=app_id,
            package=package,
            destination=uploaded.destination.get_secret_value(),
        )
