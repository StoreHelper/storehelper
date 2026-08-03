from __future__ import annotations

import json
import plistlib
import zipfile
from pathlib import Path

import httpx
import pytest

from storehelper.credentials.models import AppleApiKey
from storehelper.stores.apple.adapter import AppleAdapter
from storehelper.stores.apple.auth import AppleAuth
from storehelper.stores.apple.client import AppleClient
from storehelper.stores.apple.errors import AppleVendorError
from storehelper.stores.apple.package import validate_ipa
from storehelper.stores.models import StoreName, StoreTarget


def _target() -> StoreTarget:
    return StoreTarget(
        store=StoreName.APPLE,
        label="Apple App Store",
        app_id="app-123",
        package_name="com.example.wallet.ios",
        credential_profile="apple-team",
        language="zh-Hans",
        release_id="version-456",
        platform="IOS",
    )


def _artifact(tmp_path: Path):
    path = tmp_path / "Wallet.ipa"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "Payload/Wallet.app/Info.plist",
            plistlib.dumps(
                {
                    "CFBundleIdentifier": "com.example.wallet.ios",
                    "CFBundleShortVersionString": "1.2.3",
                    "CFBundleVersion": "42",
                }
            ),
        )
        archive.writestr("Payload/Wallet.app/Wallet", b"binary-content")
    return validate_ipa(path)


def _credential(private_key: str) -> AppleApiKey:
    return AppleApiKey(
        key_type="team",
        key_id="APPLEKEY1",
        issuer_id="issuer-1",
        private_key=private_key,
    )


def _adapter(
    private_key: str,
    transport: httpx.AsyncBaseTransport,
) -> tuple[AppleAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    return AppleAdapter(AppleClient(auth=AppleAuth(_credential(private_key)), http=http)), http


def _operation(
    offset: int,
    length: int,
    part: int,
    *,
    delivered: bool = False,
) -> dict[str, object]:
    value: dict[str, object] = {
        "method": "PUT",
        "url": f"https://uploads.example/part-{part}?signature=private-{part}",
        "offset": offset,
        "length": length,
        "partNumber": part,
        "expiration": "2099-08-03T09:00:00Z",
        "requestHeaders": [
            {"name": "Content-Type", "value": "application/octet-stream"},
            {"name": "x-upload-token", "value": f"upload-secret-{part}"},
        ],
    }
    if delivered:
        value["entityTag"] = f"etag-secret-{part}"
    return value


def _file_payload(artifact_size: int, *, delivered_first: bool = False) -> dict[str, object]:
    split = artifact_size // 2
    return {
        "data": {
            "type": "buildUploadFiles",
            "id": "file-789",
            "attributes": {
                "assetType": "ASSET",
                "fileName": "Wallet.ipa",
                "fileSize": artifact_size,
                "uti": "com.apple.ipa",
                "uploadOperations": [
                    _operation(0, split, 1, delivered=delivered_first),
                    _operation(split, artifact_size - split, 2),
                ],
            },
        }
    }


@pytest.mark.asyncio
async def test_creates_streams_and_commits_native_build_upload(
    tmp_path: Path,
    p256_private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(tmp_path)
    expected = artifact.path.read_bytes()
    requests: list[httpx.Request] = []

    def forbidden(path: Path) -> bytes:
        raise AssertionError(f"upload read the full IPA: {path}")

    monkeypatch.setattr(Path, "read_bytes", forbidden)

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "uploads.example":
            assert "authorization" not in request.headers
            await request.aread()
            return httpx.Response(200)
        if request.method == "GET" and request.url.path.endswith("/buildUploads"):
            return httpx.Response(200, json={"data": []})
        if request.method == "POST" and request.url.path == "/v1/buildUploads":
            return httpx.Response(
                201,
                json={"data": {"type": "buildUploads", "id": "upload-456"}},
            )
        if request.method == "POST" and request.url.path == "/v1/buildUploadFiles":
            return httpx.Response(201, json=_file_payload(artifact.size))
        if request.method == "PATCH":
            return httpx.Response(200, json=_file_payload(artifact.size))
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    adapter._verified_version_string = "1.2.3"
    async with http:
        uploaded = await adapter.upload(target=_target(), artifact=artifact)

    assert uploaded.artifact_id == "upload-456"
    build_request = next(
        request
        for request in requests
        if request.method == "POST" and request.url.path == "/v1/buildUploads"
    )
    assert json.loads(await build_request.aread()) == {
        "data": {
            "type": "buildUploads",
            "attributes": {
                "cfBundleShortVersionString": "1.2.3",
                "cfBundleVersion": "42",
                "platform": "IOS",
            },
            "relationships": {"app": {"data": {"type": "apps", "id": "app-123"}}},
        }
    }
    file_request = next(
        request
        for request in requests
        if request.method == "POST" and request.url.path == "/v1/buildUploadFiles"
    )
    assert json.loads(await file_request.aread())["data"]["attributes"] == {
        "assetType": "ASSET",
        "fileName": "Wallet.ipa",
        "fileSize": artifact.size,
        "uti": "com.apple.ipa",
    }
    delivery = [request for request in requests if request.url.host == "uploads.example"]
    split = artifact.size // 2
    assert await delivery[0].aread() == expected[:split]
    assert await delivery[1].aread() == expected[split:]
    commit = next(request for request in requests if request.method == "PATCH")
    assert json.loads(await commit.aread()) == {
        "data": {
            "type": "buildUploadFiles",
            "id": "file-789",
            "attributes": {
                "sourceFileChecksums": {"file": {"algorithm": "SHA_256", "hash": artifact.sha256}},
                "uploaded": True,
            },
        }
    }


@pytest.mark.asyncio
async def test_reuses_exact_reservation_and_skips_delivered_operation(
    tmp_path: Path,
    p256_private_key: str,
) -> None:
    artifact = _artifact(tmp_path)
    methods: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append((request.method, request.url.path))
        if request.url.host == "uploads.example":
            return httpx.Response(200)
        if request.url.path.endswith("/buildUploads"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "type": "buildUploads",
                            "id": "upload-existing",
                            "attributes": {
                                "cfBundleShortVersionString": "1.2.3",
                                "cfBundleVersion": "42",
                                "platform": "IOS",
                                "state": {"state": "AWAITING_UPLOAD"},
                            },
                        }
                    ]
                },
            )
        if request.method == "GET" and request.url.path.endswith("/buildUploadFiles"):
            payload = _file_payload(artifact.size, delivered_first=True)
            return httpx.Response(200, json={"data": [payload["data"]]})
        if request.method == "PATCH":
            return httpx.Response(200, json=_file_payload(artifact.size))
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    adapter._verified_version_string = "1.2.3"
    async with http:
        uploaded = await adapter.upload(target=_target(), artifact=artifact)

    assert uploaded.artifact_id == "upload-existing"
    assert ("POST", "/v1/buildUploads") not in methods
    assert ("POST", "/v1/buildUploadFiles") not in methods
    assert sum(path.startswith("/part-") for method, path in methods if method == "PUT") == 1


@pytest.mark.asyncio
async def test_transfer_interruption_rerun_reuses_the_same_server_reservation(
    tmp_path: Path,
    p256_private_key: str,
) -> None:
    artifact = _artifact(tmp_path)
    upload_searches = 0
    part_two_attempts = 0
    posts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upload_searches, part_two_attempts
        if request.method == "GET" and request.url.path.endswith("/buildUploads"):
            upload_searches += 1
            if upload_searches == 1:
                return httpx.Response(200, json={"data": []})
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "type": "buildUploads",
                            "id": "upload-456",
                            "attributes": {
                                "cfBundleShortVersionString": "1.2.3",
                                "cfBundleVersion": "42",
                                "platform": "IOS",
                                "state": {"state": "AWAITING_UPLOAD"},
                            },
                        }
                    ]
                },
            )
        if request.method == "POST" and request.url.path == "/v1/buildUploads":
            posts.append(request.url.path)
            return httpx.Response(
                201,
                json={"data": {"type": "buildUploads", "id": "upload-456"}},
            )
        if request.method == "POST" and request.url.path == "/v1/buildUploadFiles":
            posts.append(request.url.path)
            return httpx.Response(201, json=_file_payload(artifact.size))
        if request.method == "GET" and request.url.path.endswith("/buildUploadFiles"):
            payload = _file_payload(artifact.size, delivered_first=True)
            return httpx.Response(200, json={"data": [payload["data"]]})
        if request.url.host == "uploads.example":
            if request.url.path == "/part-2":
                part_two_attempts += 1
                if part_two_attempts == 1:
                    return httpx.Response(500)
            return httpx.Response(200)
        if request.method == "PATCH":
            return httpx.Response(200, json=_file_payload(artifact.size))
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    adapter._verified_version_string = "1.2.3"
    async with http:
        with pytest.raises(AppleVendorError):
            await adapter.upload(target=_target(), artifact=artifact)
        uploaded = await adapter.upload(target=_target(), artifact=artifact)

    assert uploaded.artifact_id == "upload-456"
    assert posts == ["/v1/buildUploads", "/v1/buildUploadFiles"]
    assert part_two_attempts == 2


@pytest.mark.asyncio
async def test_rejects_ambiguous_upload_reconciliation_before_mutation(
    tmp_path: Path,
    p256_private_key: str,
) -> None:
    artifact = _artifact(tmp_path)
    methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        attributes = {
            "cfBundleShortVersionString": "1.2.3",
            "cfBundleVersion": "42",
            "platform": "IOS",
            "state": {"state": "AWAITING_UPLOAD"},
        }
        return httpx.Response(
            200,
            json={
                "data": [
                    {"type": "buildUploads", "id": "upload-1", "attributes": attributes},
                    {"type": "buildUploads", "id": "upload-2", "attributes": attributes},
                ]
            },
        )

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    adapter._verified_version_string = "1.2.3"
    async with http:
        with pytest.raises(AppleVendorError) as raised:
            await adapter.upload(target=_target(), artifact=artifact)

    assert raised.value.code == "APPLE_UPLOAD_RECONCILIATION_AMBIGUOUS"
    assert methods == ["GET"]


@pytest.mark.asyncio
async def test_delivery_redirect_is_rejected_without_commit(
    tmp_path: Path,
    p256_private_key: str,
) -> None:
    artifact = _artifact(tmp_path)
    committed = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal committed
        if request.url.host == "uploads.example":
            return httpx.Response(307, headers={"location": "https://evil.example"})
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        if request.url.path == "/v1/buildUploads":
            return httpx.Response(201, json={"data": {"type": "buildUploads", "id": "upload-456"}})
        if request.url.path == "/v1/buildUploadFiles":
            return httpx.Response(201, json=_file_payload(artifact.size))
        committed = True
        return httpx.Response(200, json={"data": {}})

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    adapter._verified_version_string = "1.2.3"
    async with http:
        with pytest.raises(AppleVendorError) as raised:
            await adapter.upload(target=_target(), artifact=artifact)

    assert raised.value.code == "APPLE_DELIVERY_FAILED"
    assert committed is False


@pytest.mark.asyncio
async def test_rejects_bundle_or_marketing_version_before_upload_network(
    tmp_path: Path,
    p256_private_key: str,
) -> None:
    artifact = _artifact(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("mismatch reached the network")

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    adapter._verified_version_string = "9.9.9"
    async with http:
        with pytest.raises(AppleVendorError) as version:
            await adapter.upload(target=_target(), artifact=artifact)
        with pytest.raises(AppleVendorError) as bundle:
            await adapter.upload(
                target=_target().model_copy(update={"package_name": "com.example.other"}),
                artifact=artifact,
            )

    assert version.value.code == "APPLE_VERSION_MISMATCH"
    assert bundle.value.code == "APPLE_BUNDLE_MISMATCH"
