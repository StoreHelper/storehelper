from __future__ import annotations

import asyncio
import json
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

import httpx
import pytest

from storehelper.artifacts.models import ArtifactInfo
from storehelper.credentials.models import HonorApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.honor.adapter import HonorAdapter
from storehelper.stores.honor.auth import HonorAuth
from storehelper.stores.honor.client import HONOR_API_BASE, HonorClient
from storehelper.stores.honor.errors import HonorVendorError
from storehelper.stores.honor.package import validate_honor_artifact
from storehelper.stores.models import StoreName, StoreTarget


def _target() -> StoreTarget:
    return StoreTarget(
        store=StoreName.HONOR,
        label="HONOR App Market",
        app_id="com.example.wallet",
        package_name="com.example.wallet",
        credential_profile="release",
        language="zh-CN",
        version_code=43,
    )


def _artifact(tmp_path: Path) -> ArtifactInfo:
    apk = tmp_path / "wallet release.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
        archive.writestr("assets/payload.bin", b"payload" * 1024)
    return validate_honor_artifact(apk)


def _detail() -> dict[str, object]:
    return {
        "basicInfo": {"appId": 123456, "packageName": "com.example.wallet"},
        "languageInfo": [
            {
                "languageId": "zh-CN",
                "appName": "示例钱包",
                "intro": "已有应用介绍",
                "briefIntro": "已有简介",
                "newFeature": "旧版本说明",
            }
        ],
        "fileInfo": [],
        "releaseInfo": {"versionCode": 42},
    }


def _client(
    transport: httpx.AsyncBaseTransport,
) -> tuple[HonorAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    credential = HonorApiCredential(
        client_id="honor-client-sensitive",
        client_secret="honor-secret-sensitive",
    )
    auth = HonorAuth(credential, http, clock=lambda: 1_700_000_000.0)
    client = HonorClient(
        auth=auth,
        http=http,
        clock=lambda: 1_700_000_000.0,
        sleeper=lambda _: asyncio.sleep(0),
    )
    return HonorAdapter(client), http


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
async def test_honor_allocates_exact_apk_and_streams_to_fixed_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(tmp_path)
    expected = artifact.path.read_bytes()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        requests.append(request)
        if request.url.host == "iam.developer.honor.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "honor-token-sensitive",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        assert request.headers["authorization"] == "Bearer honor-token-sensitive"
        if request.url.path.endswith("/get-app-id"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [{"appId": 123456, "packageName": "com.example.wallet"}],
                },
            )
        if request.url.path.endswith("/get-app-detail"):
            return httpx.Response(200, json={"code": 0, "data": _detail()})
        if request.url.path.endswith("/get-app-current-release"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "appId": 123456,
                        "releaseId": "release-42",
                        "versionCode": 42,
                        "auditResult": 1,
                    },
                },
            )
        if request.url.path.endswith("/get-file-upload-url"):
            assert request.method == "POST"
            assert dict(request.url.params) == {"appId": "123456"}
            assert json.loads(request.content) == [
                {
                    "fileName": artifact.logical_name,
                    "fileType": 100,
                    "fileSize": artifact.size,
                    "fileSha256": artifact.sha256,
                }
            ]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [
                        {
                            "fileName": artifact.logical_name,
                            "uploadUrl": "https://attacker.example/must-be-ignored",
                            "objectId": 987654321,
                            "expireTime": 1_700_003_600,
                        }
                    ],
                },
            )
        if request.url.path.endswith("/file-upload"):
            return httpx.Response(200, json={"code": 0, "msg": "ok"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: (_ for _ in ()).throw(AssertionError("upload buffered the whole APK")),
    )
    adapter, http = _client(httpx.MockTransport(handler))
    try:
        uploaded = await adapter.upload(target=_target(), artifact=artifact)
    finally:
        await http.aclose()

    assert uploaded.operation_id == "123456"
    assert uploaded.artifact_id == f"987654321:{artifact.sha256}"
    upload = requests[-1]
    assert str(upload.url).startswith(f"{HONOR_API_BASE}/openapi/v1/publish/file-upload?")
    assert dict(upload.url.params) == {"appId": "123456", "objectId": "987654321"}
    assert set(_parts(upload)) == {"file"}
    assert _parts(upload)["file"] == (artifact.logical_name, expected)
    assert all(request.url.host != "attacker.example" for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        [],
        [
            {
                "fileName": "wrong.apk",
                "uploadUrl": "https://example.invalid/upload",
                "objectId": 987654321,
                "expireTime": 1_700_003_600,
            }
        ],
        [
            {
                "fileName": "wallet-release.apk",
                "uploadUrl": "",
                "objectId": 987654321,
                "expireTime": 1_700_003_600,
            }
        ],
        [
            {
                "fileName": "wallet-release.apk",
                "uploadUrl": "https://example.invalid/upload",
                "objectId": 0,
                "expireTime": 1_700_003_600,
            }
        ],
        [
            {
                "fileName": "wallet-release.apk",
                "uploadUrl": "https://example.invalid/upload",
                "objectId": 987654321,
                "expireTime": 1_699_999_999,
            }
        ],
    ],
)
async def test_honor_rejects_invalid_allocation_before_upload(tmp_path: Path, data: object) -> None:
    artifact = _artifact(tmp_path)
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "iam.developer.honor.com":
            return httpx.Response(
                200,
                json={"access_token": "token", "expires_in": 3600, "token_type": "Bearer"},
            )
        calls.append(request.url.path)
        return httpx.Response(200, json={"code": 0, "data": data})

    _, http = _client(httpx.MockTransport(handler))
    client = HonorClient(
        auth=HonorAuth(
            HonorApiCredential(client_id="client", client_secret="secret"),
            http,
            clock=lambda: 1_700_000_000.0,
        ),
        http=http,
        clock=lambda: 1_700_000_000.0,
    )
    try:
        with pytest.raises(HonorVendorError) as raised:
            await client.allocate_upload(app_id=123456, artifact=artifact)
    finally:
        await http.aclose()

    assert raised.value.code == "HONOR_UPLOAD_ALLOCATION_INVALID"
    assert calls == ["/openapi/v1/publish/get-file-upload-url"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["allocation", "upload"])
@pytest.mark.parametrize(
    "mode", ["redirect", "response-loss", "server", "unauthorized", "vendor", "malformed"]
)
async def test_honor_upload_mutations_are_never_retried_and_errors_are_safe(
    tmp_path: Path, stage: str, mode: str
) -> None:
    artifact = _artifact(tmp_path)
    mutation_calls = 0
    leaked = "honor-client-sensitive honor-secret-sensitive honor-token-sensitive"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal mutation_calls
        if request.url.host == "iam.developer.honor.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "honor-token-sensitive",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        is_allocation = request.url.path.endswith("/get-file-upload-url")
        if (stage == "allocation" and is_allocation) or (
            stage == "upload" and request.url.path.endswith("/file-upload")
        ):
            mutation_calls += 1
            if mode == "redirect":
                return httpx.Response(302, headers={"location": f"https://evil.example/{leaked}"})
            if mode == "server":
                return httpx.Response(503, json={"code": 40000, "msg": leaked})
            if mode == "unauthorized":
                return httpx.Response(401, json={"code": 10003, "msg": leaked})
            if mode == "vendor":
                return httpx.Response(200, json={"code": 30010, "msg": leaked})
            if mode == "malformed":
                return httpx.Response(200, content=b"not-json")
            raise httpx.ReadError(leaked, request=request)
        assert is_allocation
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": [
                    {
                        "fileName": artifact.logical_name,
                        "uploadUrl": "https://example.invalid/ignored",
                        "objectId": 987654321,
                        "expireTime": 1_700_003_600,
                    }
                ],
            },
        )

    _, http = _client(httpx.MockTransport(handler))
    client = HonorClient(
        auth=HonorAuth(
            HonorApiCredential(
                client_id="honor-client-sensitive",
                client_secret="honor-secret-sensitive",
            ),
            http,
            clock=lambda: 1_700_000_000.0,
        ),
        http=http,
        clock=lambda: 1_700_000_000.0,
        sleeper=lambda _: asyncio.sleep(0),
    )
    try:
        with pytest.raises(HonorVendorError) as raised:
            allocation = await client.allocate_upload(app_id=123456, artifact=artifact)
            await client.upload_file(
                app_id=123456,
                object_id=allocation.object_id,
                artifact=artifact,
            )
    finally:
        await http.aclose()

    assert mutation_calls == 1
    assert leaked not in str(raised.value)
    if mode in {"redirect", "response-loss", "server"}:
        assert raised.value.exit_code is ExitCode.NETWORK


@pytest.mark.asyncio
async def test_honor_upload_cancellation_propagates_without_retry(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.host == "iam.developer.honor.com":
            return httpx.Response(
                200,
                json={"access_token": "token", "expires_in": 3600, "token_type": "Bearer"},
            )
        calls += 1
        raise asyncio.CancelledError

    _, http = _client(httpx.MockTransport(handler))
    client = HonorClient(
        auth=HonorAuth(HonorApiCredential(client_id="client", client_secret="secret"), http),
        http=http,
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await client.allocate_upload(app_id=123456, artifact=artifact)
    finally:
        await http.aclose()

    assert calls == 1
