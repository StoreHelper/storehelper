from __future__ import annotations

import asyncio

import httpx
import pytest

from storehelper.credentials.models import HonorApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.honor.auth import HONOR_TOKEN_URL, HonorAuth
from storehelper.stores.honor.errors import HonorVendorError


def _credential() -> HonorApiCredential:
    return HonorApiCredential(client_id="known-honor-client", client_secret="known-honor-secret")


@pytest.mark.asyncio
async def test_honor_token_uses_exact_fixed_form_and_caches_until_skew() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = (await request.aread()).decode()
        assert request.method == "POST"
        assert str(request.url) == HONOR_TOKEN_URL
        assert body == (
            "grant_type=client_credentials&client_id=known-honor-client&"
            "client_secret=known-honor-secret"
        )
        return httpx.Response(
            200,
            json={"access_token": "known-honor-token", "expires_in": 3600, "token_type": "Bearer"},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth = HonorAuth(_credential(), http, clock=lambda: 1_700_000_000.0)
    try:
        first = await auth.headers()
        second = await auth.headers()
    finally:
        await http.aclose()

    assert first == second == {"Authorization": "Bearer known-honor-token"}
    assert len(requests) == 1
    assert "known-honor" not in repr(auth)


@pytest.mark.asyncio
async def test_honor_force_refresh_and_concurrent_calls_do_not_duplicate_tokens() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return httpx.Response(
            200,
            json={"access_token": f"token-{calls}", "expires_in": 3600, "token_type": "Bearer"},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth = HonorAuth(_credential(), http, clock=lambda: 1_700_000_000.0)
    try:
        values = await asyncio.gather(auth.headers(), auth.headers(), auth.headers())
        refreshed = await auth.headers(force_refresh=True)
    finally:
        await http.aclose()

    assert values == [{"Authorization": "Bearer token-1"}] * 3
    assert refreshed == {"Authorization": "Bearer token-2"}
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, expected_code, exit_code",
    [
        (
            httpx.Response(302, headers={"location": "https://evil.example/token"}),
            "HONOR_AUTH_REDIRECT",
            ExitCode.NETWORK,
        ),
        (
            httpx.Response(401, json={"error": "known-honor-secret"}),
            "HONOR_AUTH_REJECTED",
            ExitCode.AUTHENTICATION,
        ),
        (
            httpx.Response(503, json={"error": "known-honor-secret"}),
            "HONOR_AUTH_REJECTED",
            ExitCode.NETWORK,
        ),
        (
            httpx.Response(200, content=b"not-json"),
            "HONOR_AUTH_RESPONSE_INVALID",
            ExitCode.AUTHENTICATION,
        ),
        (
            httpx.Response(200, json={"access_token": "x", "expires_in": 0}),
            "HONOR_AUTH_RESPONSE_INVALID",
            ExitCode.AUTHENTICATION,
        ),
    ],
)
async def test_honor_token_failures_are_safe(
    response: httpx.Response, expected_code: str, exit_code: ExitCode
) -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
    auth = HonorAuth(_credential(), http)
    try:
        with pytest.raises(HonorVendorError) as raised:
            await auth.headers()
    finally:
        await http.aclose()

    assert raised.value.code == expected_code
    assert raised.value.exit_code is exit_code
    assert "known-honor-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_honor_token_transport_error_is_safe() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("known-honor-secret", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(HonorVendorError) as raised:
            await HonorAuth(_credential(), http).headers()
    finally:
        await http.aclose()

    assert raised.value.code == "HONOR_AUTH_NETWORK_ERROR"
    assert raised.value.exit_code is ExitCode.NETWORK
    assert "known-honor-secret" not in str(raised.value)
