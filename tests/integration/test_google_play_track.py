from __future__ import annotations

import json

import httpx
import pytest

from storehelper.credentials.models import GoogleServiceAccount
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.google_play.adapter import GooglePlayAdapter
from storehelper.stores.google_play.auth import GoogleAuth
from storehelper.stores.google_play.client import GooglePlayClient
from storehelper.stores.google_play.errors import GoogleVendorError
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


def _target(*, status: str = "draft") -> StoreTarget:
    return StoreTarget(
        store=StoreName.GOOGLE_PLAY,
        label=f"Google Play (internal, {status})",
        app_id="com.example.wallet",
        package_name="com.example.wallet",
        credential_profile="google-release",
        language="en-US",
        track="internal",
        release_status=status,
    )


def _token() -> httpx.Response:
    return httpx.Response(
        200,
        json={"access_token": "google-token", "token_type": "Bearer", "expires_in": 3600},
    )


def _edit() -> dict[str, str]:
    return {"id": "edit-123", "expiryTimeSeconds": "1786000000"}


def _adapter(
    private_key: str,
    transport: httpx.AsyncBaseTransport,
) -> tuple[GooglePlayAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    auth = GoogleAuth(_credential(private_key), http)
    return GooglePlayAdapter(GooglePlayClient(auth=auth, http=http)), http


@pytest.mark.asyncio
async def test_draft_preserves_existing_releases_and_adds_only_requested_fields(
    rsa_private_key: str,
) -> None:
    existing = {
        "name": "existing",
        "versionCodes": ["41"],
        "releaseNotes": [{"language": "en-US", "text": "old"}],
        "status": "completed",
        "inAppUpdatePriority": 3,
    }
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        requests.append(request)
        if request.url.path.endswith("/edits/edit-123"):
            return httpx.Response(200, json=_edit())
        if request.method == "GET":
            return httpx.Response(200, json={"track": "internal", "releases": [existing]})
        body = json.loads(await request.aread())
        return httpx.Response(200, json=body)

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        await adapter.prepare_release(
            target=_target(),
            artifact_id="42",
            operation_id="edit-123",
            release_notes="  Safer release  ",
        )

    assert [request.method for request in requests] == ["GET", "GET", "PUT"]
    assert requests[0].url.path.endswith("/applications/com.example.wallet/edits/edit-123")
    assert requests[1].url.path.endswith(
        "/applications/com.example.wallet/edits/edit-123/tracks/internal"
    )
    body = json.loads(await requests[2].aread())
    assert body == {
        "track": "internal",
        "releases": [
            existing,
            {
                "versionCodes": ["42"],
                "releaseNotes": [{"language": "en-US", "text": "Safer release"}],
                "status": "draft",
            },
        ],
    }


@pytest.mark.asyncio
async def test_completed_replaces_track_without_unrelated_rollout_fields(
    rsa_private_key: str,
) -> None:
    put_body: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal put_body
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        if request.url.path.endswith("/edits/edit-123"):
            return httpx.Response(200, json=_edit())
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "track": "internal",
                    "releases": [{"versionCodes": ["41"], "status": "completed"}],
                },
            )
        put_body = json.loads(await request.aread())
        return httpx.Response(200, json=put_body)

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        await adapter.prepare_release(
            target=_target(status="completed"),
            artifact_id="42",
            operation_id="edit-123",
            release_notes=None,
        )

    assert put_body == {
        "track": "internal",
        "releases": [{"versionCodes": ["42"], "status": "completed"}],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("rollout_status", ["inProgress", "halted"])
async def test_refuses_to_overwrite_an_active_staged_rollout(
    rsa_private_key: str,
    rollout_status: str,
) -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        methods.append(request.method)
        if request.url.path.endswith("/edits/edit-123"):
            return httpx.Response(200, json=_edit())
        return httpx.Response(
            200,
            json={
                "track": "internal",
                "releases": [{"versionCodes": ["41"], "status": rollout_status}],
            },
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await adapter.prepare_release(
                target=_target(),
                artifact_id="42",
                operation_id="edit-123",
                release_notes=None,
            )

    assert raised.value.code == "GOOGLE_ACTIVE_ROLLOUT"
    assert methods == ["GET", "GET"]


@pytest.mark.asyncio
async def test_identical_existing_release_makes_retry_idempotent(
    rsa_private_key: str,
) -> None:
    methods: list[str] = []
    release = {
        "versionCodes": ["42"],
        "releaseNotes": [{"language": "en-US", "text": "Retry-safe"}],
        "status": "draft",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        methods.append(request.method)
        if request.url.path.endswith("/edits/edit-123"):
            return httpx.Response(200, json=_edit())
        return httpx.Response(200, json={"track": "internal", "releases": [release]})

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        await adapter.prepare_release(
            target=_target(),
            artifact_id="42",
            operation_id="edit-123",
            release_notes="Retry-safe",
        )

    assert methods == ["GET", "GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("artifact_id", "operation_id", "notes", "code"),
    [
        ("42", None, None, "GOOGLE_EDIT_ID_MISSING"),
        ("not-a-version", "edit-123", None, "GOOGLE_VERSION_CODE_INVALID"),
        ("0", "edit-123", None, "GOOGLE_VERSION_CODE_INVALID"),
        ("42", "edit-123", " ", "GOOGLE_RELEASE_NOTES_INVALID"),
        ("42", "edit-123", "x" * 501, "GOOGLE_RELEASE_NOTES_INVALID"),
    ],
)
async def test_track_preparation_validates_durable_ids_and_notes_before_network(
    rsa_private_key: str,
    artifact_id: str,
    operation_id: str | None,
    notes: str | None,
    code: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid preparation attempted network access")

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await adapter.prepare_release(
                target=_target(),
                artifact_id=artifact_id,
                operation_id=operation_id,
                release_notes=notes,
            )

    assert raised.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "track_payload",
    [
        {"track": "other", "releases": []},
        {"track": "internal", "releases": "invalid"},
        {"track": "internal", "releases": [{"status": "completed"}]},
    ],
)
async def test_malformed_track_is_rejected(
    rsa_private_key: str,
    track_payload: dict[str, object],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        if request.url.path.endswith("/edits/edit-123"):
            return httpx.Response(200, json=_edit())
        return httpx.Response(200, json=track_payload)

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await adapter.prepare_release(
                target=_target(),
                artifact_id="42",
                operation_id="edit-123",
                release_notes=None,
            )

    assert raised.value.code == "GOOGLE_RESPONSE_INVALID"


@pytest.mark.asyncio
async def test_track_update_response_must_confirm_exact_version(
    rsa_private_key: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        if request.url.path.endswith("/edits/edit-123"):
            return httpx.Response(200, json=_edit())
        if request.method == "GET":
            return httpx.Response(200, json={"track": "internal", "releases": []})
        return httpx.Response(
            200,
            json={
                "track": "internal",
                "releases": [{"versionCodes": ["999"], "status": "draft"}],
            },
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await adapter.prepare_release(
                target=_target(),
                artifact_id="42",
                operation_id="edit-123",
                release_notes=None,
            )

    assert raised.value.code == "GOOGLE_RESPONSE_INVALID"
    assert raised.value.exit_code is ExitCode.VENDOR_REJECTION
