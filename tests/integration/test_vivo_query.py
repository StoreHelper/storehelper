from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from storehelper.credentials.models import VivoApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.vivo.auth import VivoAuth
from storehelper.stores.vivo.client import DEFAULT_API_URL, MAX_RESPONSE_BYTES, VivoClient
from storehelper.stores.vivo.errors import VivoVendorError


def _credential() -> VivoApiCredential:
    return VivoApiCredential(
        access_key=SecretStr("vivo-access-sensitive"),
        secret_key=SecretStr("vivo-secret-sensitive"),
    )


def _application(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "packageName": "com.example.wallet",
        "versionCode": "42",
        "status": 5,
    }
    value.update(updates)
    return value


def _client(
    transport: httpx.AsyncBaseTransport,
    *,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
) -> tuple[VivoClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    auth = VivoAuth(_credential(), clock=lambda: 1_700_000_000.987)
    if sleeper is None:
        client = VivoClient(auth=auth, http=http)
    else:
        client = VivoClient(auth=auth, http=http, sleeper=sleeper)
    return client, http


@pytest.mark.asyncio
async def test_application_query_uses_exact_fixed_https_gateway_and_signed_form() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        await request.aread()
        return httpx.Response(200, json={"code": 0, "subCode": "0", "data": _application()})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        info = await client.application_info("com.example.wallet")

    assert info.package_name == "com.example.wallet"
    assert info.version_code == 42
    assert info.status == 5
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == DEFAULT_API_URL
    assert request.method == "POST"
    form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
    assert form["method"] == "app.query.details"
    assert form["packageName"] == "com.example.wallet"
    assert form["access_key"] == "vivo-access-sensitive"
    assert form["timestamp"] == "1700000000987"
    assert form["format"] == "json"
    assert form["v"] == "1.0"
    assert form["sign_method"] == "HMAC-SHA256"
    assert form["target_app_key"] == "developer"
    assert len(form["sign"]) == 64


@pytest.mark.asyncio
async def test_read_query_retries_transport_429_and_5xx_with_bounds() -> None:
    attempts = 0
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("secret_key=must-not-leak", request=request)
        if attempts == 2:
            return httpx.Response(429, headers={"retry-after": "600"})
        return httpx.Response(503)

    client, http = _client(httpx.MockTransport(handler), sleeper=sleeper)
    async with http:
        with pytest.raises(VivoVendorError) as raised:
            await client.application_info("com.example.wallet")

    assert attempts == 3
    assert sleeps == [1.0, 60.0]
    assert raised.value.code == "VIVO_SERVICE_UNAVAILABLE"
    assert "must-not-leak" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "code"),
    [
        (_application(packageName="com.attacker.other"), "VIVO_PACKAGE_MISMATCH"),
        (_application(versionCode="not-numeric"), "VIVO_APPLICATION_INFO_INVALID"),
        (_application(versionCode=0), "VIVO_APPLICATION_INFO_INVALID"),
        (_application(status=99), "VIVO_APPLICATION_INFO_INVALID"),
        ({"versionCode": 42, "status": 5}, "VIVO_APPLICATION_INFO_INVALID"),
    ],
)
async def test_application_query_rejects_invalid_identity_version_or_status(
    data: dict[str, object], code: str
) -> None:
    client, http = _client(
        httpx.MockTransport(lambda request: httpx.Response(200, json={"code": "0", "data": data}))
    )
    async with http:
        with pytest.raises(VivoVendorError) as raised:
            await client.application_info("com.example.wallet")

    assert raised.value.code == code
    assert "com.attacker.other" not in str(raised.value)
    assert "not-numeric" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redirect", "non-json", "oversized", "vendor"])
async def test_query_failures_are_bounded_and_fully_redacted(mode: str) -> None:
    leaked = "access_key=must-never-print secret_key=must-never-print"

    def handler(request: httpx.Request) -> httpx.Response:
        if mode == "redirect":
            return httpx.Response(302, headers={"location": f"https://attacker.example/{leaked}"})
        if mode == "non-json":
            return httpx.Response(200, text=leaked)
        if mode == "oversized":
            return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
        return httpx.Response(
            200,
            json={"code": 9, "subCode": "UNKNOWN", "msg": leaked},
        )

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(VivoVendorError) as raised:
            await client.application_info("com.example.wallet")

    assert leaked not in str(raised.value)
    if mode == "redirect":
        assert raised.value.code == "VIVO_REDIRECT"
        assert raised.value.exit_code is ExitCode.NETWORK
    elif mode == "oversized":
        assert raised.value.code == "VIVO_RESPONSE_TOO_LARGE"
    elif mode == "non-json":
        assert raised.value.code == "VIVO_RESPONSE_INVALID"
    else:
        assert raised.value.code == "VIVO_API_REJECTED"
