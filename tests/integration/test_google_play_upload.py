from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx
import pytest

from storehelper.credentials.models import GoogleServiceAccount
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.google_play.adapter import GooglePlayAdapter
from storehelper.stores.google_play.auth import GoogleAuth
from storehelper.stores.google_play.client import GooglePlayClient
from storehelper.stores.google_play.errors import GoogleVendorError
from storehelper.stores.google_play.package import validate_google_play_artifact
from storehelper.stores.models import StoreName, StoreTarget


def _credential(private_key: str) -> GoogleServiceAccount:
    return GoogleServiceAccount.model_validate(
        {
            "type": "service_account",
            "project_id": "demo-project",
            "private_key_id": "google-key-1",
            "private_key": private_key,
            "client_email": "storehelper@demo-project.iam.gserviceaccount.com",
        }
    )


def _target() -> StoreTarget:
    return StoreTarget(
        store=StoreName.GOOGLE_PLAY,
        label="Google Play (internal, draft)",
        app_id="com.example.wallet",
        package_name="com.example.wallet",
        credential_profile="google-release",
        language="en-US",
        track="internal",
        release_status="draft",
    )


def _artifact(tmp_path: Path, suffix: str):
    path = tmp_path / f"wallet-release{suffix}"
    with zipfile.ZipFile(path, "w") as archive:
        if suffix == ".aab":
            archive.writestr("BundleConfig.pb", b"config")
            archive.writestr("base/manifest/AndroidManifest.xml", b"manifest")
        else:
            archive.writestr("AndroidManifest.xml", b"manifest")
            archive.writestr("META-INF/CERT.RSA", b"signature")
        archive.writestr("assets/payload.bin", b"payload" * 1024)
    return validate_google_play_artifact(path)


def _token() -> httpx.Response:
    return httpx.Response(
        200,
        json={"access_token": "google-token", "token_type": "Bearer", "expires_in": 3600},
    )


def _adapter(
    private_key: str,
    transport: httpx.AsyncBaseTransport,
) -> tuple[GooglePlayAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    auth = GoogleAuth(_credential(private_key), http)
    return GooglePlayAdapter(GooglePlayClient(auth=auth, http=http)), http


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "endpoint"),
    [(".aab", "bundles"), (".apk", "apks")],
)
async def test_creates_edit_and_streams_aab_or_apk_to_media_endpoint(
    tmp_path: Path,
    rsa_private_key: str,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    endpoint: str,
) -> None:
    artifact = _artifact(tmp_path, suffix)
    expected_bytes = artifact.path.read_bytes()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        if request.url.path.endswith("/edits"):
            assert json.loads(await request.aread()) == {}
            return httpx.Response(
                200,
                json={"id": "edit-123", "expiryTimeSeconds": "1786000000"},
            )
        body = await request.aread()
        assert body == expected_bytes
        assert request.headers["content-type"] == "application/octet-stream"
        assert request.headers["content-length"] == str(artifact.size)
        assert request.extensions["timeout"]["write"] >= 120.0
        response: dict[str, object] = {
            "versionCode": 42,
            "sha256": artifact.sha256,
        }
        if suffix == ".apk":
            response = {
                "versionCode": 42,
                "binary": {"sha256": artifact.sha256},
            }
        return httpx.Response(200, json=response)

    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: (_ for _ in ()).throw(AssertionError("upload buffered whole file")),
    )
    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        uploaded = await adapter.upload(target=_target(), artifact=artifact)

    assert uploaded.artifact_id == "42"
    assert uploaded.operation_id == "edit-123"
    upload = requests[-1]
    assert upload.method == "POST"
    assert upload.url.path == (
        f"/upload/androidpublisher/v3/applications/com.example.wallet/edits/edit-123/{endpoint}"
    )
    assert dict(upload.url.params) == {"uploadType": "media"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("edit_payload", "upload_payload"),
    [
        ({}, None),
        ({"id": "edit-1", "expiryTimeSeconds": "0"}, None),
        ({"id": "edit-1", "expiryTimeSeconds": "1786000000"}, {}),
        (
            {"id": "edit-1", "expiryTimeSeconds": "1786000000"},
            {"versionCode": 0, "sha256": "a" * 64},
        ),
        (
            {"id": "edit-1", "expiryTimeSeconds": "1786000000"},
            {"versionCode": 42, "sha256": "b" * 64},
        ),
    ],
)
async def test_rejects_malformed_edit_or_upload_response(
    tmp_path: Path,
    rsa_private_key: str,
    edit_payload: dict[str, object],
    upload_payload: dict[str, object] | None,
) -> None:
    artifact = _artifact(tmp_path, ".aab")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        if request.url.path.endswith("/edits"):
            return httpx.Response(200, json=edit_payload)
        assert upload_payload is not None
        return httpx.Response(200, json=upload_payload)

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await adapter.upload(target=_target(), artifact=artifact)

    assert raised.value.code == "GOOGLE_RESPONSE_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redirect", "response-loss", "server-error"])
async def test_upload_failure_is_safe_and_never_replays_media_automatically(
    tmp_path: Path,
    rsa_private_key: str,
    mode: str,
) -> None:
    artifact = _artifact(tmp_path, ".aab")
    uploads = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal uploads
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        if request.url.path.endswith("/edits"):
            return httpx.Response(
                200,
                json={"id": "edit-1", "expiryTimeSeconds": "1786000000"},
            )
        uploads += 1
        await request.aread()
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "https://attacker.example"})
        if mode == "server-error":
            return httpx.Response(503, json={"error": {"message": "private_key=secret"}})
        raise httpx.ReadError("access_token=secret", request=request)

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await adapter.upload(target=_target(), artifact=artifact)

    assert uploads == 1
    assert raised.value.exit_code is ExitCode.NETWORK
    assert "secret" not in str(raised.value)
