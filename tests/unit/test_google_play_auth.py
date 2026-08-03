from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from storehelper.credentials.models import GoogleServiceAccount
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.google_play.auth import (
    ANDROID_PUBLISHER_SCOPE,
    GoogleAuth,
    GoogleAuthError,
)


def _decode(segment: str) -> dict[str, object]:
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def _credential(private_key: str) -> GoogleServiceAccount:
    return GoogleServiceAccount.model_validate(
        {
            "type": "service_account",
            "project_id": "demo-project",
            "private_key_id": "google-key-1",
            "private_key": private_key,
            "client_email": "storehelper@demo-project.iam.gserviceaccount.com",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


def test_assertion_has_expected_rs256_header_claims_and_signature(
    rsa_private_key: str,
) -> None:
    now = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    auth = GoogleAuth(_credential(rsa_private_key), http)

    token = auth.assertion(now=now)
    encoded_header, encoded_payload, encoded_signature = token.split(".")

    assert _decode(encoded_header) == {
        "alg": "RS256",
        "kid": "google-key-1",
        "typ": "JWT",
    }
    assert _decode(encoded_payload) == {
        "iss": "storehelper@demo-project.iam.gserviceaccount.com",
        "scope": ANDROID_PUBLISHER_SCOPE,
        "aud": "https://oauth2.googleapis.com/token",
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + 3600,
    }
    signature = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
    private_key = serialization.load_pem_private_key(rsa_private_key.encode(), password=None)
    assert isinstance(private_key, rsa.RSAPrivateKey)
    private_key.public_key().verify(
        signature,
        f"{encoded_header}.{encoded_payload}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


@pytest.mark.asyncio
async def test_exchanges_assertion_as_form_and_caches_access_token(
    rsa_private_key: str,
) -> None:
    requests: list[httpx.Request] = []
    tokens = iter(("access-token-1", "access-token-2"))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": next(tokens),
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

    now = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        auth = GoogleAuth(_credential(rsa_private_key), http)

        first = await auth.access_token(now=now)
        cached = await auth.access_token(now=now + timedelta(seconds=3539))
        renewed = await auth.access_token(now=now + timedelta(seconds=3541))

    assert (first, cached, renewed) == (
        "access-token-1",
        "access-token-1",
        "access-token-2",
    )
    assert len(requests) == 2
    assert all(
        request.url == httpx.URL("https://oauth2.googleapis.com/token") for request in requests
    )
    form = httpx.QueryParams(requests[0].content.decode())
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    assertion = form["assertion"]
    assert _decode(assertion.split(".")[1])["scope"] == ANDROID_PUBLISHER_SCOPE


@pytest.mark.asyncio
async def test_force_refresh_replaces_cached_token(rsa_private_key: str) -> None:
    tokens = iter(("access-token-1", "access-token-2"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"access_token": next(tokens), "token_type": "Bearer", "expires_in": 3600},
        )

    now = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        auth = GoogleAuth(_credential(rsa_private_key), http)
        first = await auth.access_token(now=now)
        refreshed = await auth.access_token(now=now, force_refresh=True)

    assert first == "access-token-1"
    assert refreshed == "access-token-2"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"access_token": "token", "expires_in": 0},
        {"access_token": "token", "expires_in": "not-a-number"},
        {"access_token": "token", "expires_in": 3600, "token_type": "MAC"},
    ],
)
async def test_rejects_malformed_token_responses_without_leaking_payload(
    rsa_private_key: str,
    payload: dict[str, object],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        auth = GoogleAuth(_credential(rsa_private_key), http)
        with pytest.raises(GoogleAuthError) as raised:
            await auth.access_token()

    assert raised.value.code == "GOOGLE_AUTH_RESPONSE_INVALID"
    assert "not-a-number" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redirect", "rejected", "non-json", "network"])
async def test_token_exchange_failures_are_typed_and_secret_safe(
    rsa_private_key: str,
    mode: str,
) -> None:
    leaked = "access_token=must-never-print"

    def handler(request: httpx.Request) -> httpx.Response:
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "https://attacker.example/token"})
        if mode == "rejected":
            return httpx.Response(401, json={"error_description": leaked})
        if mode == "non-json":
            return httpx.Response(200, text=leaked)
        raise httpx.ConnectError(leaked, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        auth = GoogleAuth(_credential(rsa_private_key), http)
        with pytest.raises(GoogleAuthError) as raised:
            await auth.access_token()

    expected = ExitCode.NETWORK if mode in ("redirect", "network") else ExitCode.AUTHENTICATION
    assert raised.value.exit_code is expected
    assert leaked not in str(raised.value)
    assert rsa_private_key not in repr(auth)
