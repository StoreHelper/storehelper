from __future__ import annotations

import json

import httpx
import pytest

from storehelper.credentials.models import AppleApiKey
from storehelper.stores.apple.adapter import AppleAdapter
from storehelper.stores.apple.auth import AppleAuth
from storehelper.stores.apple.client import AppleClient
from storehelper.stores.apple.errors import AppleVendorError
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


def _adapter(
    private_key: str,
    handler: httpx.AsyncBaseTransport,
) -> tuple[AppleAdapter, httpx.AsyncClient]:
    credential = AppleApiKey(
        key_type="team",
        key_id="APPLEKEY1",
        issuer_id="issuer-1",
        private_key=private_key,
    )
    http = httpx.AsyncClient(transport=handler)
    return AppleAdapter(AppleClient(auth=AppleAuth(credential), http=http)), http


@pytest.mark.asyncio
async def test_attaches_build_and_omits_localization_when_notes_are_absent(
    p256_private_key: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    async with http:
        await adapter.prepare_release(
            target=_target(),
            artifact_id="build-999",
            release_notes=None,
        )

    assert len(requests) == 1
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == "/v1/appStoreVersions/version-456/relationships/build"
    assert json.loads(await requests[0].aread()) == {"data": {"type": "builds", "id": "build-999"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("notes", ["x", "界" * 4000])
async def test_updates_only_whats_new_for_the_unique_locale(
    p256_private_key: str,
    notes: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/relationships/build"):
            return httpx.Response(204)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "type": "appStoreVersionLocalizations",
                            "id": "localization-1",
                            "attributes": {
                                "locale": "zh-Hans",
                                "description": "preserve me",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "type": "appStoreVersionLocalizations",
                    "id": "localization-1",
                }
            },
        )

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    async with http:
        await adapter.prepare_release(
            target=_target(),
            artifact_id="build-999",
            release_notes=notes,
        )

    lookup = requests[1]
    assert dict(lookup.url.params) == {
        "filter[locale]": "zh-Hans",
        "fields[appStoreVersionLocalizations]": "locale",
        "limit": "200",
    }
    update = requests[2]
    assert json.loads(await update.aread()) == {
        "data": {
            "type": "appStoreVersionLocalizations",
            "id": "localization-1",
            "attributes": {"whatsNew": notes},
        }
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 2])
async def test_requires_exactly_one_configured_localization(
    p256_private_key: str,
    count: int,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/relationships/build"):
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "type": "appStoreVersionLocalizations",
                        "id": f"localization-{index}",
                        "attributes": {"locale": "zh-Hans"},
                    }
                    for index in range(count)
                ]
            },
        )

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(AppleVendorError) as raised:
            await adapter.prepare_release(
                target=_target(),
                artifact_id="build-999",
                release_notes="Fixes",
            )

    assert raised.value.code == "APPLE_LOCALIZATION_NOT_UNIQUE"


@pytest.mark.asyncio
@pytest.mark.parametrize("notes", ["", " ", "x" * 4001])
async def test_rejects_invalid_notes_before_attaching_build(
    p256_private_key: str,
    notes: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid notes reached App Store Connect")

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(AppleVendorError) as raised:
            await adapter.prepare_release(
                target=_target(),
                artifact_id="build-999",
                release_notes=notes,
            )

    assert raised.value.code == "APPLE_RELEASE_NOTES_INVALID"
