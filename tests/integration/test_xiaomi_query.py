from __future__ import annotations

import json

import httpx
import pytest

from storehelper.credentials.models import XiaomiApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.models import StoreName, StoreTarget
from storehelper.stores.xiaomi.adapter import XiaomiAdapter
from storehelper.stores.xiaomi.auth import XiaomiAuth
from storehelper.stores.xiaomi.client import XiaomiClient
from storehelper.stores.xiaomi.errors import XiaomiVendorError


def _credential(certificate: str) -> XiaomiApiCredential:
    return XiaomiApiCredential(
        username="developer@example.com",
        api_secret="api-secret",
        public_key_certificate=certificate,
    )


def _target(**updates: object) -> StoreTarget:
    values: dict[str, object] = {
        "store": StoreName.XIAOMI,
        "label": "Xiaomi App Store",
        "app_id": "com.example.wallet",
        "package_name": "com.example.wallet",
        "credential_profile": "xiaomi-release",
        "language": "zh-CN",
        "app_name": "Example Wallet",
        "icon_path": "/tmp/icon.png",
        "privacy_url": "https://example.com/privacy",
    }
    values.update(updates)
    return StoreTarget.model_validate(values)


def _success(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "result": 0,
        "message": "查询成功",
        "updateVersion": True,
        "updateInfo": True,
        "create": False,
        "packageInfo": {
            "appName": "Example Wallet",
            "packageName": "com.example.wallet",
            "versionCode": 42,
            "versionName": "1.2.3",
        },
    }
    value.update(updates)
    return value


def _adapter(
    certificate: str,
    transport: httpx.AsyncBaseTransport,
    **client_kwargs: object,
) -> tuple[XiaomiAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    credential = _credential(certificate)
    client = XiaomiClient(
        auth=XiaomiAuth(credential),
        credential=credential,
        http=http,
        **client_kwargs,
    )
    return XiaomiAdapter(client), http


@pytest.mark.asyncio
async def test_verify_posts_exact_signed_query_to_fixed_https_endpoint(
    rsa_public_certificate: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_success())

    adapter, http = _adapter(rsa_public_certificate, httpx.MockTransport(handler))
    async with http:
        verified = await adapter.verify(target=_target())

    assert verified.app_id == "com.example.wallet"
    assert verified.package_name == "com.example.wallet"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url == "https://api.developer.xiaomi.com/devupload/dev/query"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    body = request.content
    assert b'name="RequestData"' in body
    assert b'name="SIG"' in body
    expected = json.dumps(
        {"packageName": "com.example.wallet", "userName": "developer@example.com"},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    assert expected in body
    assert b'name="apk"' not in body


@pytest.mark.asyncio
async def test_query_retries_transport_and_transient_responses_only_with_bounds(
    rsa_public_certificate: str,
) -> None:
    attempts = 0
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("api_secret=leak", request=request)
        if attempts == 2:
            return httpx.Response(429, headers={"retry-after": "600"})
        return httpx.Response(200, json=_success())

    adapter, http = _adapter(
        rsa_public_certificate,
        httpx.MockTransport(handler),
        sleeper=sleeper,
    )
    async with http:
        await adapter.verify(target=_target())

    assert attempts == 3
    assert sleeps == [1.0, 60.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"result": -7, "message": "api_secret=leak"}, "XIAOMI_PACKAGE_CLAIM_REQUIRED"),
        (_success(packageInfo=None), "XIAOMI_PACKAGE_NOT_FOUND"),
        (
            _success(packageInfo={"packageName": "com.attacker.other", "appName": "Other"}),
            "XIAOMI_PACKAGE_MISMATCH",
        ),
        (_success(updateVersion=False), "XIAOMI_UPDATE_NOT_ALLOWED"),
        (_success(updateVersion="true"), "XIAOMI_RESPONSE_INVALID"),
        ({"message": "missing result"}, "XIAOMI_RESPONSE_INVALID"),
    ],
)
async def test_query_rejects_claim_missing_mismatch_not_ready_and_malformed_results(
    rsa_public_certificate: str,
    payload: dict[str, object],
    code: str,
) -> None:
    adapter, http = _adapter(
        rsa_public_certificate,
        httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    async with http:
        with pytest.raises(XiaomiVendorError) as raised:
            await adapter.verify(target=_target())

    assert raised.value.code == code
    assert "api_secret=leak" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redirect", "non-json", "http", "network-exhausted"])
async def test_query_transport_and_protocol_failures_are_safe(
    rsa_public_certificate: str,
    mode: str,
) -> None:
    leaked = "api_secret=must-never-print"

    def handler(request: httpx.Request) -> httpx.Response:
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "https://attacker.example"})
        if mode == "non-json":
            return httpx.Response(200, text=leaked)
        if mode == "http":
            return httpx.Response(403, json={"message": leaked})
        raise httpx.ConnectError(leaked, request=request)

    async def sleeper(seconds: float) -> None:
        return None

    adapter, http = _adapter(
        rsa_public_certificate,
        httpx.MockTransport(handler),
        sleeper=sleeper,
    )
    async with http:
        with pytest.raises(XiaomiVendorError) as raised:
            await adapter.verify(target=_target())

    assert leaked not in str(raised.value)
    if mode in {"redirect", "network-exhausted"}:
        assert raised.value.exit_code is ExitCode.NETWORK


@pytest.mark.asyncio
async def test_invalid_xiaomi_target_is_rejected_before_network(
    rsa_public_certificate: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid target attempted network access")

    adapter, http = _adapter(rsa_public_certificate, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(XiaomiVendorError) as raised:
            await adapter.verify(target=_target(app_id="com.example.other"))

    assert raised.value.code == "XIAOMI_TARGET_INVALID"
