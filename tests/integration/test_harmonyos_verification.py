from __future__ import annotations

import httpx
import pytest

from storehelper.credentials.models import HuaweiServiceAccount
from storehelper.stores.harmonyos.adapter import HarmonyOSAdapter
from storehelper.stores.harmonyos.client import HarmonyOSClient
from storehelper.stores.huawei.auth import HuaweiAuth
from storehelper.stores.huawei.errors import HuaweiVendorError
from storehelper.stores.models import StoreName, StoreTarget


def _target() -> StoreTarget:
    return StoreTarget(
        store=StoreName.HARMONYOS,
        label="Huawei AppGallery (HarmonyOS)",
        app_id="100000002",
        package_name="com.example.wallet.harmony",
        credential_profile="company",
        language="zh-CN",
    )


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
@pytest.mark.parametrize(
    "appids",
    [
        [{"value": "100000002"}],
        [{"appId": "100000002"}],
        ["100000002"],
    ],
)
async def test_verifies_existing_harmonyos_app_with_package_type_seven(
    rsa_private_key: str,
    appids: list[object],
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ret": {"code": 0}, "appids": appids})

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        verified = await adapter.verify(target=_target())

    assert verified.app_id == "100000002"
    assert verified.package_name == "com.example.wallet.harmony"
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path.endswith("/api/publish/v2/appid-list")
    assert dict(request.url.params) == {
        "packageName": "com.example.wallet.harmony",
        "packageTypes": "7",
    }
    assert request.headers["client_id"] == "key-1"
    assert request.headers["authorization"].startswith("Bearer ")


@pytest.mark.asyncio
async def test_rejects_configured_app_id_not_returned_by_huawei(
    rsa_private_key: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ret": {"code": 0}, "appids": [{"value": "different"}]},
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(HuaweiVendorError) as raised:
            await adapter.verify(target=_target())

    assert raised.value.code == "HARMONYOS_APP_NOT_FOUND"


@pytest.mark.asyncio
async def test_refreshes_authentication_once_after_unauthorized(
    rsa_private_key: str,
) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(401, json={"ret": {"code": 1101}})
        return httpx.Response(
            200,
            json={"ret": {"code": 0}, "appids": [{"value": "100000002"}]},
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        await adapter.verify(target=_target())

    assert attempts == 2


@pytest.mark.asyncio
async def test_invalid_response_does_not_echo_raw_body(
    rsa_private_key: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="private_key=never-print")

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(HuaweiVendorError) as raised:
            await adapter.verify(target=_target())

    assert "never-print" not in str(raised.value)
