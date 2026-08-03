from __future__ import annotations

import httpx
import pytest

from storehelper.credentials.models import GoogleServiceAccount
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.google_play.adapter import GooglePlayAdapter
from storehelper.stores.google_play.auth import GoogleAuth
from storehelper.stores.google_play.client import GooglePlayClient
from storehelper.stores.google_play.errors import GoogleVendorError
from storehelper.stores.models import ReviewStatus, StoreName, StoreTarget


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


def _target(**updates: object) -> StoreTarget:
    values: dict[str, object] = {
        "store": StoreName.GOOGLE_PLAY,
        "label": "Google Play (internal, draft)",
        "app_id": "com.example.wallet",
        "package_name": "com.example.wallet",
        "credential_profile": "google-release",
        "language": "en-US",
        "track": "internal",
        "release_status": "draft",
    }
    values.update(updates)
    return StoreTarget.model_validate(values)


def _adapter(
    private_key: str,
    transport: httpx.AsyncBaseTransport,
    **client_kwargs: object,
) -> tuple[GooglePlayAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    auth = GoogleAuth(_credential(private_key), http)
    return GooglePlayAdapter(GooglePlayClient(auth=auth, http=http, **client_kwargs)), http


def _token() -> httpx.Response:
    return httpx.Response(
        200,
        json={"access_token": "google-token", "token_type": "Bearer", "expires_in": 3600},
    )


@pytest.mark.asyncio
async def test_verify_and_status_use_only_read_only_lifecycle_endpoint(
    rsa_private_key: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        return httpx.Response(
            200,
            json={
                "releases": [
                    {
                        "releaseName": "1.2.3",
                        "track": "internal",
                        "activeArtifacts": [{"versionCode": 42}],
                        "releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_IN_REVIEW",
                    }
                ]
            },
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        verified = await adapter.verify(target=_target())
        status = await adapter.review_status(target=_target())

    assert verified.app_id == "com.example.wallet"
    assert verified.package_name == "com.example.wallet"
    assert status is ReviewStatus.IN_REVIEW
    api_requests = [request for request in requests if request.url.host != "oauth2.googleapis.com"]
    assert len(api_requests) == 2
    assert all(request.method == "GET" for request in api_requests)
    assert all("/edits" not in request.url.path for request in api_requests)
    assert all(
        request.url.path
        == "/androidpublisher/v3/applications/com.example.wallet/tracks/internal/releases"
        for request in api_requests
    )


@pytest.mark.asyncio
async def test_empty_track_still_verifies_package_access(rsa_private_key: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return (
            _token()
            if request.url.host == "oauth2.googleapis.com"
            else httpx.Response(200, json={})
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        verified = await adapter.verify(target=_target())
        status = await adapter.review_status(target=_target())

    assert verified.package_name == "com.example.wallet"
    assert status is ReviewStatus.UNKNOWN


@pytest.mark.asyncio
async def test_package_and_track_path_segments_are_percent_encoded(
    rsa_private_key: str,
) -> None:
    raw_paths: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        raw_paths.append(request.url.raw_path)
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GooglePlayClient(auth=GoogleAuth(_credential(rsa_private_key), http), http=http)
    async with http:
        await client.list_releases(
            package_name="com.example/wallet",
            track="wear:production",
        )

    assert raw_paths == [
        b"/androidpublisher/v3/applications/com.example%2Fwallet/tracks/wear%3Aproduction/releases"
    ]


@pytest.mark.asyncio
async def test_refreshes_access_token_once_after_api_401(rsa_private_key: str) -> None:
    oauth_tokens = iter(("token-1", "token-2"))
    api_authorizations: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "access_token": next(oauth_tokens),
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        api_authorizations.append(request.headers["authorization"])
        if len(api_authorizations) == 1:
            return httpx.Response(401, json={"error": {"status": "UNAUTHENTICATED"}})
        return httpx.Response(200, json={})

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        await adapter.verify(target=_target())

    assert api_authorizations == ["Bearer token-1", "Bearer token-2"]


@pytest.mark.asyncio
async def test_retries_transient_api_responses_with_bounded_delay(
    rsa_private_key: str,
) -> None:
    attempts = 0
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, headers={"retry-after": "600"})
        return httpx.Response(200, json={})

    adapter, http = _adapter(
        rsa_private_key,
        httpx.MockTransport(handler),
        sleeper=sleeper,
    )
    async with http:
        await adapter.verify(target=_target())

    assert attempts == 3
    assert sleeps == [60.0, 60.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redirect", "non-json", "forbidden", "network"])
async def test_verification_failures_are_safe_and_typed(
    rsa_private_key: str,
    mode: str,
) -> None:
    leaked = "access_token=must-never-print"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "https://attacker.example"})
        if mode == "non-json":
            return httpx.Response(200, text=leaked)
        if mode == "forbidden":
            return httpx.Response(403, json={"error": {"message": leaked}})
        raise httpx.ConnectError(leaked, request=request)

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await adapter.verify(target=_target())

    assert leaked not in str(raised.value)
    if mode in ("redirect", "network"):
        assert raised.value.exit_code is ExitCode.NETWORK


@pytest.mark.asyncio
async def test_invalid_google_target_is_rejected_before_network(rsa_private_key: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid target attempted network access")

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await adapter.verify(target=_target(track=None))

    assert raised.value.code == "GOOGLE_TARGET_INVALID"
