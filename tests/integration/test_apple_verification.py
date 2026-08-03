from __future__ import annotations

import httpx
import pytest

from storehelper.credentials.models import AppleApiKey
from storehelper.stores.apple.adapter import AppleAdapter
from storehelper.stores.apple.auth import AppleAuth
from storehelper.stores.apple.client import AppleClient
from storehelper.stores.apple.errors import AppleVendorError
from storehelper.stores.models import StoreName, StoreTarget


def _target(**updates: object) -> StoreTarget:
    values: dict[str, object] = {
        "store": StoreName.APPLE,
        "label": "Apple App Store",
        "app_id": "app-123",
        "package_name": "com.example.wallet.ios",
        "credential_profile": "apple-team",
        "language": "zh-Hans",
        "release_id": "version-456",
        "platform": "IOS",
    }
    values.update(updates)
    return StoreTarget.model_validate(values)


def _credential(private_key: str) -> AppleApiKey:
    return AppleApiKey.model_validate(
        {
            "key_type": "team",
            "key_id": "APPLEKEY1",
            "issuer_id": "issuer-1",
            "private_key": private_key,
        }
    )


def _app_payload(*, bundle_id: str = "com.example.wallet.ios") -> dict[str, object]:
    return {
        "data": {
            "type": "apps",
            "id": "app-123",
            "attributes": {"bundleId": bundle_id, "name": "Wallet"},
        }
    }


def _version_payload(
    *,
    app_id: str = "app-123",
    platform: str = "IOS",
    version: str = "1.2.3",
) -> dict[str, object]:
    return {
        "data": {
            "type": "appStoreVersions",
            "id": "version-456",
            "attributes": {"platform": platform, "versionString": version},
            "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
        }
    }


def _adapter(
    private_key: str,
    transport: httpx.AsyncBaseTransport,
    **client_kwargs: object,
) -> tuple[AppleAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    client = AppleClient(
        auth=AppleAuth(_credential(private_key)),
        http=http,
        **client_kwargs,
    )
    return AppleAdapter(client), http


@pytest.mark.asyncio
async def test_verifies_existing_app_version_relationship_and_bundle_id(
    p256_private_key: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/apps/app-123":
            return httpx.Response(200, json=_app_payload())
        return httpx.Response(200, json=_version_payload())

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    async with http:
        verified = await adapter.verify(target=_target())

    assert verified.app_id == "app-123"
    assert verified.package_name == "com.example.wallet.ios"
    assert adapter.verified_version_string == "1.2.3"
    assert [request.url.path for request in requests] == [
        "/v1/apps/app-123",
        "/v1/appStoreVersions/version-456",
    ]
    assert all(request.headers["authorization"].startswith("Bearer ") for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("app_payload", "version_payload", "code"),
    [
        (_app_payload(bundle_id="com.example.other"), _version_payload(), "APPLE_BUNDLE_MISMATCH"),
        (_app_payload(), _version_payload(app_id="other-app"), "APPLE_VERSION_APP_MISMATCH"),
        (_app_payload(), _version_payload(platform="MAC_OS"), "APPLE_PLATFORM_MISMATCH"),
        (_app_payload(), {"data": []}, "APPLE_RESPONSE_INVALID"),
    ],
)
async def test_rejects_mismatched_or_malformed_targets(
    p256_private_key: str,
    app_payload: dict[str, object],
    version_payload: dict[str, object],
    code: str,
) -> None:
    responses = iter([app_payload, version_payload])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(AppleVendorError) as raised:
            await adapter.verify(target=_target())

    assert raised.value.code == code


@pytest.mark.asyncio
async def test_refreshes_jwt_once_after_401(p256_private_key: str) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(401, json={"errors": [{"status": "401"}]})
        if request.url.path.startswith("/v1/apps/"):
            return httpx.Response(200, json=_app_payload())
        return httpx.Response(200, json=_version_payload())

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    async with http:
        await adapter.verify(target=_target())

    assert attempts == 3


@pytest.mark.asyncio
async def test_retries_transient_responses_with_bounded_retry_after(
    p256_private_key: str,
) -> None:
    attempts = 0
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, headers={"retry-after": "120"}, json={"errors": []})
        return httpx.Response(200, json=_app_payload())

    _, http = _adapter(
        p256_private_key,
        httpx.MockTransport(handler),
        sleeper=sleeper,
    )
    client = AppleClient(auth=AppleAuth(_credential(p256_private_key)), http=http, sleeper=sleeper)
    async with http:
        result = await client.request_json("GET", "/v1/apps/app-123")

    assert result["data"]
    assert attempts == 3
    assert sleeps == [60.0, 60.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(302, headers={"location": "https://evil.example"}), "APPLE_REDIRECT"),
        (httpx.Response(200, text="private_key=never-print"), "APPLE_RESPONSE_INVALID"),
        (
            httpx.Response(
                403,
                json={"errors": [{"detail": "access_token=never-print"}]},
            ),
            "APPLE_AUTHORIZATION_FAILED",
        ),
    ],
)
async def test_rejects_redirect_malformed_and_secret_error_bodies(
    p256_private_key: str,
    response: httpx.Response,
    code: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return response

    _, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    client = AppleClient(auth=AppleAuth(_credential(p256_private_key)), http=http)
    async with http:
        with pytest.raises(AppleVendorError) as raised:
            await client.request_json("GET", "/v1/apps/app-123")

    assert raised.value.code == code
    assert "never-print" not in str(raised.value)
