from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
from pydantic import SecretStr

from storehelper.credentials.models import OppoApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.oppo.auth import OppoAuth
from storehelper.stores.oppo.client import DEFAULT_API_BASE, OppoClient
from storehelper.stores.oppo.errors import OppoVendorError


def _credential() -> OppoApiCredential:
    return OppoApiCredential(
        client_id=SecretStr("oppo-client-sensitive"),
        client_secret=SecretStr("oppo-secret-sensitive"),
    )


def _application(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "pkg_name": "com.example.wallet",
        "version_code": "42",
        "audit_status": 111,
        "app_name": "Example Wallet",
        "second_category_id": "463",
        "third_category_id": "6648",
        "summary": "Safe payments",
        "detail_desc": "A complete existing application description.",
        "privacy_source_url": "https://example.com/privacy",
        "icon_url": "https://cdn.example.com/icon.png",
        "pic_url": "https://cdn.example.com/one.png,https://cdn.example.com/two.png",
        "age_level": "18",
        "adaptive_equipment": "4",
        "copyright_url": "https://cdn.example.com/copyright.pdf",
        "business_username": "Release Owner",
        "business_email": "release@example.com",
        "business_mobile": "13800138000",
    }
    value.update(updates)
    return value


def _token(value: str = "oppo-access-sensitive", expires: object = 7200) -> dict[str, object]:
    return {
        "errno": 0,
        "data": {"access_token": value, "expire_in": expires},
    }


def _client(
    transport: httpx.AsyncBaseTransport,
    *,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
) -> tuple[OppoClient, httpx.AsyncClient]:
    credential = _credential()
    http = httpx.AsyncClient(transport=transport)
    auth = OppoAuth(credential, clock=lambda: 1_700_000_000.0)
    if sleeper is None:
        client = OppoClient(credential=credential, auth=auth, http=http)
    else:
        client = OppoClient(credential=credential, auth=auth, http=http, sleeper=sleeper)
    return client, http


@pytest.mark.asyncio
async def test_application_query_uses_exact_fixed_https_token_and_info_endpoints() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/developer/v1/token":
            return httpx.Response(200, json=_token())
        return httpx.Response(200, json={"errno": 0, "data": _application()})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        info = await client.application_info("com.example.wallet")

    assert info.package_name == "com.example.wallet"
    assert info.version_code == 42
    assert info.audit_status == 111
    assert len(requests) == 2
    token_request, info_request = requests
    assert str(token_request.url.copy_with(query=None)) == f"{DEFAULT_API_BASE}/developer/v1/token"
    assert dict(token_request.url.params) == {
        "client_id": "oppo-client-sensitive",
        "client_secret": "oppo-secret-sensitive",
    }
    assert str(info_request.url.copy_with(query=None)) == f"{DEFAULT_API_BASE}/resource/v1/app/info"
    assert info_request.url.params["pkg_name"] == "com.example.wallet"
    assert info_request.url.params["access_token"] == "oppo-access-sensitive"
    assert info_request.url.params["timestamp"] == "1700000000"
    assert len(info_request.url.params["api_sign"]) == 64


@pytest.mark.asyncio
async def test_read_queries_retry_transport_429_and_5xx_with_bounds() -> None:
    attempts = {"token": 0, "info": 0}
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        key = "token" if request.url.path == "/developer/v1/token" else "info"
        attempts[key] += 1
        if key == "token" and attempts[key] == 1:
            raise httpx.ConnectError("client_secret=must-not-leak", request=request)
        if key == "token" and attempts[key] == 2:
            return httpx.Response(503)
        if key == "info" and attempts[key] == 1:
            return httpx.Response(429, headers={"retry-after": "600"})
        if key == "token":
            return httpx.Response(200, json=_token())
        return httpx.Response(200, json={"errno": 0, "data": _application()})

    client, http = _client(httpx.MockTransport(handler), sleeper=sleeper)
    async with http:
        await client.application_info("com.example.wallet")

    assert attempts == {"token": 3, "info": 2}
    assert sleeps == [1.0, 2.0, 60.0]


@pytest.mark.asyncio
async def test_application_query_refreshes_token_once_after_auth_failure() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/developer/v1/token":
            token_number = calls.count("/developer/v1/token")
            return httpx.Response(200, json=_token(f"access-{token_number}"))
        if calls.count("/resource/v1/app/info") == 1:
            return httpx.Response(
                200,
                json={"errno": 910002, "data": {"message": "access-1 must-not-leak"}},
            )
        return httpx.Response(200, json={"errno": 0, "data": _application()})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        info = await client.application_info("com.example.wallet")

    assert info.version_code == 42
    assert calls == [
        "/developer/v1/token",
        "/resource/v1/app/info",
        "/developer/v1/token",
        "/resource/v1/app/info",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "code"),
    [
        (_application(pkg_name="com.attacker.other"), "OPPO_PACKAGE_MISMATCH"),
        (_application(version_code="not-numeric"), "OPPO_APPLICATION_INFO_INVALID"),
        (_application(summary=""), "OPPO_APPLICATION_INCOMPLETE"),
        ({key: value for key, value in _application().items() if key != "icon_url"},
         "OPPO_APPLICATION_INCOMPLETE"),
    ],
)
async def test_application_query_rejects_identity_version_and_incomplete_listing(
    data: dict[str, object],
    code: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/developer/v1/token":
            return httpx.Response(200, json=_token())
        return httpx.Response(200, json={"errno": 0, "data": data})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(OppoVendorError) as raised:
            await client.application_info("com.example.wallet")

    assert raised.value.code == code
    assert "com.attacker.other" not in str(raised.value)
    assert "not-numeric" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redirect", "non-json", "unknown-errno", "auth-twice"])
async def test_token_and_query_failures_are_bounded_and_fully_redacted(mode: str) -> None:
    leaked = "client_secret=must-never-print access_token=must-never-print"
    info_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal info_calls
        if request.url.path == "/developer/v1/token":
            if mode == "redirect":
                return httpx.Response(302, headers={"location": f"https://attacker.example/{leaked}"})
            return httpx.Response(200, json=_token())
        info_calls += 1
        if mode == "non-json":
            return httpx.Response(200, text=leaked)
        if mode == "unknown-errno":
            return httpx.Response(200, json={"errno": 999999, "data": {"message": leaked}})
        return httpx.Response(200, json={"errno": 910002, "data": {"message": leaked}})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(OppoVendorError) as raised:
            await client.application_info("com.example.wallet")

    assert leaked not in str(raised.value)
    if mode == "redirect":
        assert raised.value.code == "OPPO_REDIRECT"
        assert raised.value.exit_code is ExitCode.NETWORK
    if mode == "auth-twice":
        assert info_calls == 2
        assert raised.value.code == "OPPO_AUTHENTICATION_FAILED"
