from __future__ import annotations

import asyncio
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from storehelper.credentials.models import VivoApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.vivo.auth import VivoAuth
from storehelper.stores.vivo.client import DEFAULT_API_URL, VivoClient
from storehelper.stores.vivo.errors import VivoVendorError
from storehelper.stores.vivo.package import VivoArtifactInfo, validate_vivo_artifact


def _artifact(tmp_path: Path) -> VivoArtifactInfo:
    apk = tmp_path / "wallet release.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
        archive.writestr("assets/payload.bin", b"payload" * 1024)
    return validate_vivo_artifact(apk)


def _client(transport: httpx.AsyncBaseTransport) -> tuple[VivoClient, httpx.AsyncClient]:
    credential = VivoApiCredential(
        access_key=SecretStr("vivo-access-sensitive"),
        secret_key=SecretStr("vivo-secret-sensitive"),
    )
    http = httpx.AsyncClient(transport=transport)
    return VivoClient(auth=VivoAuth(credential, clock=lambda: 1_700_000_000.0), http=http), http


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
async def test_upload_streams_exact_signed_multipart_without_buffering_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(tmp_path)
    expected = artifact.path.read_bytes()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "subCode": 0,
                "data": {"serialnumber": "serial-sensitive", "fileMd5": artifact.md5},
            },
        )

    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: (_ for _ in ()).throw(AssertionError("upload buffered the whole APK")),
    )
    client, http = _client(httpx.MockTransport(handler))
    async with http:
        uploaded = await client.upload_apk(
            package_name="com.example.wallet",
            artifact=artifact,
        )

    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == DEFAULT_API_URL
    assert request.method == "POST"
    parts = _parts(request)
    assert parts["method"] == (None, b"app.upload.apk.app.64")
    assert parts["packageName"] == (None, b"com.example.wallet")
    assert parts["fileMd5"] == (None, artifact.md5.encode())
    assert parts["access_key"] == (None, b"vivo-access-sensitive")
    assert len(parts["sign"][1]) == 64
    assert parts["file"] == ("wallet-release.apk", expected)
    assert uploaded.serialnumber.get_secret_value() == "serial-sensitive"
    assert uploaded.md5.get_secret_value() == artifact.md5
    rendered = repr(uploaded) + str(uploaded)
    assert "serial-sensitive" not in rendered
    assert artifact.md5 not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redirect", "response-loss", "server", "vendor", "malformed"])
async def test_upload_failures_are_safe_and_never_retried(tmp_path: Path, mode: str) -> None:
    artifact = _artifact(tmp_path)
    calls = 0
    leaked = "access_key=must-never-print secret_key=must-never-print"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await request.aread()
        if mode == "redirect":
            return httpx.Response(302, headers={"location": f"https://attacker.example/{leaked}"})
        if mode == "server":
            return httpx.Response(503, json={"msg": leaked})
        if mode == "vendor":
            return httpx.Response(200, json={"code": 9, "subCode": "B0302", "msg": leaked})
        if mode == "malformed":
            return httpx.Response(200, json={"code": 0, "data": {}})
        raise httpx.ReadError(leaked, request=request)

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(VivoVendorError) as raised:
            await client.upload_apk(package_name="com.example.wallet", artifact=artifact)

    assert calls == 1
    assert leaked not in str(raised.value)
    if mode in {"redirect", "response-loss", "server"}:
        assert raised.value.exit_code is ExitCode.NETWORK


@pytest.mark.asyncio
async def test_upload_cancellation_propagates_without_retry(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(asyncio.CancelledError):
            await client.upload_apk(package_name="com.example.wallet", artifact=artifact)

    assert calls == 1
