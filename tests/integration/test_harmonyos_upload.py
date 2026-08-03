from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx
import pytest

from storehelper.credentials.models import HuaweiServiceAccount
from storehelper.stores.harmonyos.adapter import HarmonyOSAdapter
from storehelper.stores.harmonyos.client import HarmonyOSClient
from storehelper.stores.harmonyos.package import validate_harmonyos_artifact
from storehelper.stores.huawei.auth import HuaweiAuth
from storehelper.stores.huawei.errors import HuaweiVendorError


def _artifact(tmp_path: Path):
    path = tmp_path / "wallet.app"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("pack.info", b"{}")
        archive.writestr("entry.hap", b"signed-hap")
    return validate_harmonyos_artifact(path)


def _adapter(
    rsa_private_key: str,
    handler: httpx.AsyncBaseTransport,
) -> tuple[HarmonyOSAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler)
    auth = HuaweiAuth(
        HuaweiServiceAccount(
            key_id="key-1",
            sub_account="sub-1",
            private_key=rsa_private_key,
        )
    )
    return HarmonyOSAdapter(HarmonyOSClient(auth=auth, http=http)), http


@pytest.mark.asyncio
async def test_streams_obs_upload_and_binds_only_durable_package_id(
    tmp_path: Path,
    rsa_private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(tmp_path)
    expected_bytes = artifact.path.read_bytes()
    requests: list[httpx.Request] = []
    obs_authorization = "AWS4-HMAC-SHA256 Credential=temporary-signed-value"

    def forbidden_read_bytes(path: Path) -> bytes:
        raise AssertionError(f"upload loaded the full artifact: {path}")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/upload-url/for-obs"):
            return httpx.Response(
                200,
                json={
                    "ret": {"code": 0},
                    "urlInfo": {
                        "objectId": "CN/20260803/private-object.app",
                        "url": "https://obs.example/upload/wallet.app",
                        "method": "PUT",
                        "headers": {
                            "Authorization": obs_authorization,
                            "Content-Type": "application/octet-stream",
                            "Content-Length": str(artifact.size),
                            "x-amz-content-sha256": "STREAMING-AWS4-HMAC-SHA256-PAYLOAD",
                            "x-amz-date": "20260803T000000Z",
                        },
                    },
                },
            )
        if request.url.host == "obs.example":
            await request.aread()
            return httpx.Response(200)
        assert request.url.path.endswith("/api/publish/v3/app-package-info")
        return httpx.Response(
            200,
            json={"ret": {"code": 0}, "packageId": "package-42"},
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        uploaded = await adapter.upload(app_id="100000002", artifact=artifact)

    assert uploaded.artifact_id == "package-42"
    allocation, obs, binding = requests
    assert dict(allocation.url.params) == {
        "appId": "100000002",
        "fileName": "wallet.app",
        "contentLength": str(artifact.size),
        "releaseType": "1",
    }
    assert obs.method == "PUT"
    assert obs.headers["authorization"] == obs_authorization
    assert "client_id" not in obs.headers
    assert not obs.headers["authorization"].startswith("Bearer ")
    assert await obs.aread() == expected_bytes
    assert dict(binding.url.params) == {"appId": "100000002", "releaseType": "1"}
    assert json.loads(await binding.aread()) == {
        "fileName": "wallet.app",
        "objectId": "CN/20260803/private-object.app",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "method"),
    [
        ("http://obs.example/upload", "PUT"),
        ("https://user:pass@obs.example/upload", "PUT"),
        ("https://obs.example/upload", "POST"),
    ],
)
async def test_rejects_unsafe_obs_allocation_without_leaking_values(
    tmp_path: Path,
    rsa_private_key: str,
    url: str,
    method: str,
) -> None:
    artifact = _artifact(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ret": {"code": 0},
                "urlInfo": {
                    "objectId": "private-object-id",
                    "url": url,
                    "method": method,
                    "headers": {"Authorization": "AWS4 private-signature"},
                },
            },
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(HuaweiVendorError) as raised:
            await adapter.upload(app_id="100000002", artifact=artifact)

    message = str(raised.value)
    assert "private-object-id" not in message
    assert "private-signature" not in message


@pytest.mark.asyncio
async def test_obs_redirect_is_rejected_without_binding(
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    artifact = _artifact(tmp_path)
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/upload-url/for-obs"):
            return httpx.Response(
                200,
                json={
                    "ret": {"code": 0},
                    "urlInfo": {
                        "objectId": "private-object-id",
                        "url": "https://obs.example/upload",
                        "method": "PUT",
                        "headers": {
                            "Authorization": "AWS4 private-signature",
                            "Content-Length": str(artifact.size),
                        },
                    },
                },
            )
        return httpx.Response(302, headers={"location": "https://other.example/upload"})

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(HuaweiVendorError) as raised:
            await adapter.upload(app_id="100000002", artifact=artifact)

    assert raised.value.code == "HARMONYOS_OBS_UPLOAD_FAILED"
    assert not any(path.endswith("/app-package-info") for path in paths)
