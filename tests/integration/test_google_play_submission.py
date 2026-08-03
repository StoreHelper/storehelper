from __future__ import annotations

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


def _target() -> StoreTarget:
    return StoreTarget(
        store=StoreName.GOOGLE_PLAY,
        label="Google Play (internal, draft)",
        app_id="com.example.wallet",
        package_name="com.example.wallet",
        credential_profile="google-release",
        language="en-US",
        track="internal",
        release_status="draft",
    )


def _token() -> httpx.Response:
    return httpx.Response(
        200,
        json={"access_token": "google-token", "token_type": "Bearer", "expires_in": 3600},
    )


def _edit() -> dict[str, str]:
    return {"id": "edit-123", "expiryTimeSeconds": "1786000000"}


def _lifecycle(*, visible: bool) -> dict[str, object]:
    artifacts = [{"versionCode": 42}] if visible else [{"versionCode": 41}]
    return {
        "releases": [
            {
                "releaseName": "1.2.3",
                "track": "internal",
                "activeArtifacts": artifacts,
                "releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_DRAFT",
            }
        ]
    }


def _adapter(
    private_key: str,
    transport: httpx.AsyncBaseTransport,
    **client_kwargs: object,
) -> tuple[GooglePlayAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    auth = GoogleAuth(_credential(private_key), http)
    return GooglePlayAdapter(GooglePlayClient(auth=auth, http=http, **client_kwargs)), http


@pytest.mark.asyncio
async def test_validates_before_commit_with_explicit_safe_review_parameters(
    rsa_private_key: str,
) -> None:
    api_requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        api_requests.append(request)
        if request.url.path.endswith("/releases"):
            return httpx.Response(200, json=_lifecycle(visible=False))
        if request.url.path.endswith("/edits/edit-123"):
            return httpx.Response(200, json=_edit())
        assert await request.aread() == b""
        return httpx.Response(200, json=_edit())

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        submission_id = await adapter.submit(
            target=_target(), artifact_id="42", operation_id="edit-123"
        )

    assert submission_id == "42"
    assert [request.method for request in api_requests] == ["GET", "GET", "POST", "POST"]
    assert api_requests[2].url.path.endswith("/edits/edit-123:validate")
    commit = api_requests[3]
    assert commit.url.path.endswith("/edits/edit-123:commit")
    assert dict(commit.url.params) == {
        "changesNotSentForReview": "false",
        "changesInReviewBehavior": "ERROR_IF_IN_REVIEW",
    }


@pytest.mark.asyncio
async def test_already_visible_version_succeeds_without_touching_edit(
    rsa_private_key: str,
) -> None:
    api_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        api_paths.append(request.url.path)
        return httpx.Response(200, json=_lifecycle(visible=True))

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        submission_id = await adapter.submit(
            target=_target(), artifact_id="42", operation_id="edit-123"
        )

    assert submission_id == "42"
    assert api_paths == [
        "/androidpublisher/v3/applications/com.example.wallet/tracks/internal/releases"
    ]


@pytest.mark.asyncio
async def test_lost_commit_response_reconciles_visible_version(
    rsa_private_key: str,
) -> None:
    committed = False
    commits = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal committed, commits
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        if request.url.path.endswith("/releases"):
            return httpx.Response(200, json=_lifecycle(visible=committed))
        if request.url.path.endswith("/edits/edit-123") or request.url.path.endswith(":validate"):
            return httpx.Response(200, json=_edit())
        commits += 1
        committed = True
        raise httpx.ReadError("access_token=must-never-print", request=request)

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        submission_id = await adapter.submit(
            target=_target(), artifact_id="42", operation_id="edit-123"
        )

    assert submission_id == "42"
    assert commits == 1


@pytest.mark.asyncio
async def test_unconfirmed_commit_response_loss_is_bounded_and_resumable(
    rsa_private_key: str,
) -> None:
    lifecycle_checks = 0
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lifecycle_checks
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        if request.url.path.endswith("/releases"):
            lifecycle_checks += 1
            return httpx.Response(200, json=_lifecycle(visible=False))
        if request.url.path.endswith("/edits/edit-123") or request.url.path.endswith(":validate"):
            return httpx.Response(200, json=_edit())
        raise httpx.ReadError("lost", request=request)

    adapter, http = _adapter(
        rsa_private_key,
        httpx.MockTransport(handler),
        sleeper=sleeper,
    )
    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await adapter.submit(target=_target(), artifact_id="42", operation_id="edit-123")

    assert raised.value.code == "GOOGLE_NETWORK_ERROR"
    assert raised.value.resumable is True
    assert lifecycle_checks == 4  # one preflight plus three reconciliation checks
    assert sleeps == [1.0, 2.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("visible_after_missing", [False, True])
async def test_missing_edit_reconciles_or_reports_expired_actionably(
    rsa_private_key: str,
    visible_after_missing: bool,
) -> None:
    lifecycle_checks = 0
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lifecycle_checks
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        if request.url.path.endswith("/releases"):
            lifecycle_checks += 1
            visible = visible_after_missing and lifecycle_checks > 1
            return httpx.Response(200, json=_lifecycle(visible=visible))
        return httpx.Response(
            404,
            json={"error": {"status": "NOT_FOUND", "message": "Edit expired"}},
        )

    adapter, http = _adapter(
        rsa_private_key,
        httpx.MockTransport(handler),
        sleeper=sleeper,
    )
    async with http:
        if visible_after_missing:
            result = await adapter.submit(
                target=_target(), artifact_id="42", operation_id="edit-123"
            )
            assert result == "42"
        else:
            with pytest.raises(GoogleVendorError) as raised:
                await adapter.submit(target=_target(), artifact_id="42", operation_id="edit-123")
            assert raised.value.code == "GOOGLE_EDIT_EXPIRED"
            assert raised.value.resumable is False

    assert lifecycle_checks == (2 if visible_after_missing else 4)


@pytest.mark.asyncio
async def test_commit_conflict_is_reconciled_before_actionable_failure(
    rsa_private_key: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        if request.url.path.endswith("/releases"):
            return httpx.Response(200, json=_lifecycle(visible=False))
        if request.url.path.endswith("/edits/edit-123") or request.url.path.endswith(":validate"):
            return httpx.Response(200, json=_edit())
        return httpx.Response(
            409,
            json={"error": {"status": "ABORTED", "message": "Concurrent update"}},
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler), sleeper=_no_sleep)
    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await adapter.submit(target=_target(), artifact_id="42", operation_id="edit-123")

    assert raised.value.code == "GOOGLE_EDIT_CONFLICT"
    assert raised.value.exit_code is ExitCode.LOCAL_STATE
    assert raised.value.resumable is False


@pytest.mark.asyncio
async def test_existing_changes_in_review_preserve_the_edit_for_resume(
    rsa_private_key: str,
) -> None:
    lifecycle_checks = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lifecycle_checks
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        if request.url.path.endswith("/releases"):
            lifecycle_checks += 1
            return httpx.Response(200, json=_lifecycle(visible=False))
        if request.url.path.endswith("/edits/edit-123") or request.url.path.endswith(":validate"):
            return httpx.Response(200, json=_edit())
        return httpx.Response(
            400,
            json={
                "error": {
                    "status": "FAILED_PRECONDITION",
                    "message": "Changes are already in review",
                }
            },
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await adapter.submit(target=_target(), artifact_id="42", operation_id="edit-123")

    assert raised.value.code == "GOOGLE_CHANGES_IN_REVIEW"
    assert raised.value.resumable is True
    assert lifecycle_checks == 1


async def _no_sleep(seconds: float) -> None:
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("artifact_id", "operation_id", "code"),
    [
        ("not-a-version", "edit-123", "GOOGLE_VERSION_CODE_INVALID"),
        ("0", "edit-123", "GOOGLE_VERSION_CODE_INVALID"),
        ("42", None, "GOOGLE_EDIT_ID_MISSING"),
    ],
)
async def test_submission_requires_durable_version_and_edit_before_network(
    rsa_private_key: str,
    artifact_id: str,
    operation_id: str | None,
    code: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid submission attempted network access")

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await adapter.submit(
                target=_target(),
                artifact_id=artifact_id,
                operation_id=operation_id,
            )

    assert raised.value.code == code


@pytest.mark.asyncio
async def test_malformed_lifecycle_artifacts_are_rejected(rsa_private_key: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return _token()
        return httpx.Response(
            200,
            json={"releases": [{"activeArtifacts": [{"versionCode": "42"}]}]},
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await adapter.submit(target=_target(), artifact_id="42", operation_id="edit-123")

    assert raised.value.code == "GOOGLE_RESPONSE_INVALID"
