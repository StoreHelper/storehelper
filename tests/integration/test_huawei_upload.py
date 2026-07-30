from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx
import pytest

from storehelper.credentials.models import HuaweiServiceAccount
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.huawei.auth import HuaweiAuth
from storehelper.stores.huawei.client import HuaweiClient
from storehelper.stores.huawei.errors import HuaweiVendorError
from storehelper.stores.huawei.package import PackageInfo, validate_package


def _package(tmp_path: Path) -> PackageInfo:
    path = tmp_path / "1785240000000-12ab34cd-release.apk"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
    return validate_package(path)


def _auth(rsa_private_key: str) -> HuaweiAuth:
    return HuaweiAuth(
        HuaweiServiceAccount(
            key_id="key-1",
            sub_account="sub-1",
            private_key=rsa_private_key,
        )
    )


@pytest.mark.asyncio
async def test_upload_and_bind_uses_logical_name_and_returns_pkg_version(
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    package = _package(tmp_path)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/appid-list"):
            return httpx.Response(
                200,
                json={"ret": {"code": 0}, "appids": [{"appId": "123"}]},
            )
        if request.url.path.endswith("/upload-url"):
            return httpx.Response(
                200,
                json={
                    "ret": {"code": 0},
                    "result": {
                        "uploadUrl": "https://upload.example/files",
                        "authCode": "upload-secret",
                        "fileName": "server.apk",
                    },
                },
            )
        if request.url.host == "upload.example":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "UploadFileRsp": {
                            "ifSuccess": 1,
                            "fileInfoList": [
                                {
                                    "fileDestUlr": "https://dest.example/object",
                                    "fileName": package.logical_name,
                                }
                            ],
                        }
                    }
                },
            )
        assert request.url.path.endswith("/app-file-info")
        return httpx.Response(200, json={"ret": {"code": 0}, "pkgVersion": ["10004151"]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HuaweiClient(auth=_auth(rsa_private_key), http=http)
        await client.verify_app(app_id="123", package_name="com.example.app")
        bound = await client.upload_and_bind(app_id="123", package=package)

    assert bound.pkg_version == "10004151"
    upload_request = requests[2]
    upload_body = await upload_request.aread()
    assert b'filename="release.apk"' in upload_body
    assert b"upload-secret" in upload_body
    bind_body = json.loads(await requests[3].aread())
    assert bind_body == {
        "fileType": 5,
        "files": [
            {
                "fileName": "release.apk",
                "fileDestUrl": "https://dest.example/object",
            }
        ],
    }
    assert requests[0].headers["client_id"] == "key-1"
    assert requests[0].headers["authorization"].startswith("Bearer ")


@pytest.mark.asyncio
async def test_authenticated_request_retries_once_after_unauthorized(
    rsa_private_key: str,
) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(401, json={"ret": {"code": 1101}})
        return httpx.Response(200, json={"ret": {"code": 0}, "appids": [{"appId": "1"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HuaweiClient(auth=_auth(rsa_private_key), http=http)
        await client.verify_app(app_id="1", package_name="com.example.app")

    assert attempts == 2


@pytest.mark.asyncio
async def test_missing_upload_auth_code_is_safe(
    rsa_private_key: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ret": {"code": 0},
                "result": {"uploadUrl": "https://upload.example/files"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HuaweiClient(auth=_auth(rsa_private_key), http=http)
        with pytest.raises(HuaweiVendorError) as raised:
            await client.request_upload(app_id="123", suffix="apk")

    assert raised.value.code == "HUAWEI_RESPONSE_INVALID"
    assert "upload-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_vendor_binding_error_is_translated_without_secrets(
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    package = _package(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload-url"):
            return httpx.Response(
                200,
                json={
                    "ret": {"code": 0},
                    "result": {
                        "uploadUrl": "https://upload.example/files",
                        "authCode": "never-print-this-secret",
                    },
                },
            )
        if request.url.host == "upload.example":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "UploadFileRsp": {
                            "ifSuccess": 1,
                            "fileInfoList": [{"fileDestUrl": "https://secret.example/object"}],
                        }
                    }
                },
            )
        return httpx.Response(
            200,
            json={"ret": {"code": 204144662, "msg": "Parameter(fileName) error"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HuaweiClient(auth=_auth(rsa_private_key), http=http)
        with pytest.raises(HuaweiVendorError) as raised:
            await client.upload_and_bind(app_id="123", package=package)

    assert raised.value.vendor_code == "204144662"
    assert "never-print-this-secret" not in str(raised.value)
    assert "secret.example" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("pkg_version", [[], ["1", "2"], ""])
async def test_binding_requires_exactly_one_package_version(
    tmp_path: Path,
    rsa_private_key: str,
    pkg_version: object,
) -> None:
    package = _package(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ret": {"code": 0}, "pkgVersion": pkg_version})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HuaweiClient(auth=_auth(rsa_private_key), http=http)
        with pytest.raises(HuaweiVendorError, match="exactly one"):
            await client.bind_package(
                app_id="123",
                package=package,
                destination="https://dest.example/object",
            )


@pytest.mark.asyncio
async def test_upload_rejects_redirect_instead_of_forwarding_upload_secret(
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    package = _package(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://unsafe.example/upload"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HuaweiClient(auth=_auth(rsa_private_key), http=http)
        with pytest.raises(HuaweiVendorError) as raised:
            await client.upload_file(
                package=package,
                upload_url="https://upload.example/files",
                auth_code="upload-secret",
            )

    assert raised.value.exit_code is ExitCode.NETWORK
    assert "upload-secret" not in str(raised.value)
