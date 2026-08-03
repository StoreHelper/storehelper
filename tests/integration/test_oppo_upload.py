from __future__ import annotations

import asyncio
import hashlib
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from storehelper.credentials.models import OppoApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.oppo.auth import OppoAuth
from storehelper.stores.oppo.client import OppoClient, validate_oppo_upload_url
from storehelper.stores.oppo.errors import OppoVendorError
from storehelper.stores.oppo.package import OppoArtifactInfo, validate_oppo_artifact


def _artifact(tmp_path: Path) -> OppoArtifactInfo:
    apk = tmp_path / "wallet release.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
        archive.writestr("assets/payload.bin", b"payload" * 1024)
    return validate_oppo_artifact(apk)


def _client(
    transport: httpx.AsyncBaseTransport,
) -> tuple[OppoClient, httpx.AsyncClient]:
    credential = OppoApiCredential(
        client_id=SecretStr("oppo-client-sensitive"),
        client_secret=SecretStr("oppo-secret-sensitive"),
    )
    auth = OppoAuth(credential, clock=lambda: 1_700_000_000.0)
    auth.cache_token("oppo-access-sensitive", expires_in=7200)
    http = httpx.AsyncClient(transport=transport)
    return OppoClient(credential=credential, auth=auth, http=http), http


def _parts(request: httpx.Request) -> dict[str, tuple[str | None, bytes]]:
    message = BytesParser(policy=policy.default).parsebytes(
        (f"Content-Type: {request.headers['content-type']}\r\nMIME-Version: 1.0\r\n\r\n").encode()
        + request.content
    )
    result: dict[str, tuple[str | None, bytes]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        assert isinstance(name, str)
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        result[name] = (part.get_filename(), payload)
    return result


@pytest.mark.asyncio
async def test_allocates_safe_url_and_streams_exact_oppo_multipart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(tmp_path)
    expected = artifact.path.read_bytes()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        requests.append(request)
        if request.url.host == "oop-openapi-cn.heytapmobi.com":
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {
                        "upload_url": "https://upload-cn.oppomobile.com/v1/package",
                        "sign": "oppo-upload-sign-sensitive",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "errno": 0,
                "data": {"url": "https://cdn.oppomobile.com/private/wallet.apk"},
            },
        )

    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: (_ for _ in ()).throw(AssertionError("upload buffered the whole APK")),
    )
    client, http = _client(httpx.MockTransport(handler))
    async with http:
        uploaded = await client.upload_apk(artifact)

    assert len(requests) == 2
    allocation, upload = requests
    assert allocation.method == "GET"
    assert allocation.url.path == "/resource/v1/upload/get-upload-url"
    assert allocation.url.params["access_token"] == "oppo-access-sensitive"
    assert upload.method == "POST"
    assert str(upload.url) == "https://upload-cn.oppomobile.com/v1/package"
    parts = _parts(upload)
    assert set(parts) == {"type", "sign", "file"}
    assert parts["type"] == (None, b"apk")
    assert parts["sign"] == (None, b"oppo-upload-sign-sensitive")
    assert parts["file"] == ("wallet-release.apk", expected)
    assert uploaded.md5 == hashlib.md5(expected, usedforsecurity=False).hexdigest()
    assert uploaded.file_url.get_secret_value().endswith("/private/wallet.apk")
    rendered = repr(uploaded) + str(uploaded)
    assert "private/wallet.apk" not in rendered
    assert "oppo-upload-sign-sensitive" not in rendered


@pytest.mark.parametrize(
    "url",
    [
        "http://upload.oppomobile.com/file",
        "https://user:password@upload.oppomobile.com/file",
        "https://upload.oppomobile.com/file#fragment",
        "https://upload.oppomobile.com:8443/file",
        "https://127.0.0.1/file",
        "https://10.0.0.1/file",
        "https://169.254.1.1/file",
        "https://[::1]/file",
        "https://upload.oppomobile.com.attacker.example/file",
        "https://oppomobile.com.attacker.example/file",
        "https://heytapmobi.com.attacker.example/file",
        "https://localhost/file",
        "not-a-url",
    ],
)
def test_dynamic_upload_url_rejects_unsafe_origins(url: str) -> None:
    with pytest.raises(OppoVendorError) as raised:
        validate_oppo_upload_url(url)

    assert raised.value.code == "OPPO_UPLOAD_HOST_UNSAFE"
    assert url not in str(raised.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://upload.oppomobile.com/file",
        "https://upload.heytapmobi.com/file?part=1",
        "https://upload.heytap.com:443/file",
        "https://upload.oppomobile.cn/file",
        "https://upload.heytapmobi.cn/file",
    ],
)
def test_dynamic_upload_url_accepts_explicit_official_suffixes(url: str) -> None:
    assert validate_oppo_upload_url(url) == url


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["address-http", "address-malformed", "upload-malformed"])
async def test_upload_rejects_malformed_address_and_result_without_retries(
    tmp_path: Path,
    mode: str,
) -> None:
    artifact = _artifact(tmp_path)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await request.aread()
        if request.url.host == "oop-openapi-cn.heytapmobi.com":
            if mode == "address-http":
                return httpx.Response(503, json={"errno": 910000})
            if mode == "address-malformed":
                return httpx.Response(200, json={"errno": 0, "data": {"sign": "only-sign"}})
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {
                        "upload_url": "https://upload.oppomobile.com/file",
                        "sign": "upload-sign",
                    },
                },
            )
        return httpx.Response(200, json={"errno": 0, "data": {}})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(OppoVendorError) as raised:
            await client.upload_apk(artifact)

    assert calls == (2 if mode == "upload-malformed" else 1)
    assert raised.value.code in {"OPPO_SERVICE_UNAVAILABLE", "OPPO_RESPONSE_INVALID"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redirect", "response-loss", "server-error", "vendor"])
async def test_apk_upload_failures_are_safe_and_never_retried(
    tmp_path: Path,
    mode: str,
) -> None:
    artifact = _artifact(tmp_path)
    uploads = 0
    leaked = "upload-sign=must-never-print access_token=must-never-print"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal uploads
        await request.aread()
        if request.url.host == "oop-openapi-cn.heytapmobi.com":
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {
                        "upload_url": "https://upload.oppomobile.com/file",
                        "sign": "upload-sign",
                    },
                },
            )
        uploads += 1
        if mode == "redirect":
            return httpx.Response(302, headers={"location": f"https://attacker.example/{leaked}"})
        if mode == "server-error":
            return httpx.Response(503, json={"message": leaked})
        if mode == "vendor":
            return httpx.Response(200, json={"errno": 910005, "data": {"message": leaked}})
        raise httpx.ReadError(leaked, request=request)

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(OppoVendorError) as raised:
            await client.upload_apk(artifact)

    assert uploads == 1
    assert leaked not in str(raised.value)
    if mode != "vendor":
        assert raised.value.exit_code is ExitCode.NETWORK


@pytest.mark.asyncio
async def test_apk_upload_cancellation_propagates_without_retry(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    uploads = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal uploads
        if request.url.host == "oop-openapi-cn.heytapmobi.com":
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {
                        "upload_url": "https://upload.oppomobile.com/file",
                        "sign": "upload-sign",
                    },
                },
            )
        uploads += 1
        raise asyncio.CancelledError

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(asyncio.CancelledError):
            await client.upload_apk(artifact)

    assert uploads == 1
