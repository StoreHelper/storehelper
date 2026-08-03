from __future__ import annotations

import json

import httpx
import pytest

from storehelper.credentials.models import AppleApiKey
from storehelper.stores.apple.adapter import AppleAdapter
from storehelper.stores.apple.auth import AppleAuth
from storehelper.stores.apple.client import AppleClient
from storehelper.stores.apple.errors import AppleVendorError
from storehelper.stores.models import ReviewStatus, StoreName, StoreTarget


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


def _submission(submission_id: str = "submission-1") -> dict[str, object]:
    return {
        "type": "reviewSubmissions",
        "id": submission_id,
        "attributes": {"platform": "IOS", "state": "READY_FOR_REVIEW"},
    }


def _item(version_id: str = "version-456") -> dict[str, object]:
    return {
        "type": "reviewSubmissionItems",
        "id": "item-1",
        "relationships": {
            "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}}
        },
    }


@pytest.mark.asyncio
async def test_creates_submission_item_and_submits_for_review(
    p256_private_key: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/reviewSubmissions"):
            return httpx.Response(200, json={"data": []})
        if request.method == "POST" and request.url.path == "/v1/reviewSubmissions":
            return httpx.Response(201, json={"data": _submission()})
        if request.method == "GET" and request.url.path.endswith("/items"):
            return httpx.Response(200, json={"data": []})
        if request.method == "POST" and request.url.path == "/v1/reviewSubmissionItems":
            return httpx.Response(201, json={"data": _item()})
        if request.method == "PATCH":
            return httpx.Response(
                200,
                json={
                    "data": {
                        **_submission(),
                        "attributes": {"platform": "IOS", "state": "WAITING_FOR_REVIEW"},
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    async with http:
        submission_id = await adapter.submit(target=_target(), artifact_id="build-999")

    assert submission_id == "submission-1"
    create_submission = requests[1]
    assert json.loads(await create_submission.aread()) == {
        "data": {
            "type": "reviewSubmissions",
            "attributes": {"platform": "IOS"},
            "relationships": {"app": {"data": {"type": "apps", "id": "app-123"}}},
        }
    }
    create_item = requests[3]
    assert json.loads(await create_item.aread()) == {
        "data": {
            "type": "reviewSubmissionItems",
            "relationships": {
                "reviewSubmission": {"data": {"type": "reviewSubmissions", "id": "submission-1"}},
                "appStoreVersion": {"data": {"type": "appStoreVersions", "id": "version-456"}},
            },
        }
    }
    submit = requests[4]
    assert json.loads(await submit.aread()) == {
        "data": {
            "type": "reviewSubmissions",
            "id": "submission-1",
            "attributes": {"submitted": True},
        }
    }
    assert not any("appStoreVersionSubmissions" in request.url.path for request in requests)


@pytest.mark.asyncio
async def test_reuses_existing_draft_and_version_item(
    p256_private_key: str,
) -> None:
    methods: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append((request.method, request.url.path))
        if request.url.path.endswith("/reviewSubmissions"):
            return httpx.Response(200, json={"data": [_submission()]})
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json={"data": [_item()]})
        return httpx.Response(200, json={"data": _submission()})

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    async with http:
        assert await adapter.submit(target=_target(), artifact_id="build-999") == "submission-1"

    assert [method for method, _ in methods] == ["GET", "GET", "PATCH"]


@pytest.mark.asyncio
async def test_recovers_when_create_responses_are_lost(
    p256_private_key: str,
) -> None:
    submission_exists = False
    item_exists = False
    submission_posts = 0
    item_posts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submission_exists, item_exists, submission_posts, item_posts
        if request.method == "GET" and request.url.path.endswith("/reviewSubmissions"):
            return httpx.Response(
                200,
                json={"data": [_submission()] if submission_exists else []},
            )
        if request.method == "POST" and request.url.path == "/v1/reviewSubmissions":
            submission_posts += 1
            submission_exists = True
            raise httpx.ConnectError("response lost", request=request)
        if request.method == "GET" and request.url.path.endswith("/items"):
            return httpx.Response(200, json={"data": [_item()] if item_exists else []})
        if request.method == "POST" and request.url.path == "/v1/reviewSubmissionItems":
            item_posts += 1
            item_exists = True
            raise httpx.ConnectError("response lost", request=request)
        if request.method == "PATCH":
            return httpx.Response(200, json={"data": _submission()})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(AppleVendorError):
            await adapter.submit(target=_target(), artifact_id="build-999")
        with pytest.raises(AppleVendorError):
            await adapter.submit(target=_target(), artifact_id="build-999")
        result = await adapter.submit(target=_target(), artifact_id="build-999")

    assert result == "submission-1"
    assert submission_posts == 1
    assert item_posts == 1


@pytest.mark.asyncio
async def test_rejects_existing_item_for_a_different_version(
    p256_private_key: str,
) -> None:
    methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path.endswith("/reviewSubmissions"):
            return httpx.Response(200, json={"data": [_submission()]})
        return httpx.Response(200, json={"data": [_item("version-other")]})

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(AppleVendorError) as raised:
            await adapter.submit(target=_target(), artifact_id="build-999")

    assert raised.value.code == "APPLE_REVIEW_ITEM_VERSION_MISMATCH"
    assert methods == ["GET", "GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [409, 422])
async def test_submission_rejections_are_sanitized(
    p256_private_key: str,
    status_code: int,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/reviewSubmissions"):
            return httpx.Response(200, json={"data": [_submission()]})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [_item()]})
        return httpx.Response(
            status_code,
            json={"errors": [{"code": "STATE_ERROR", "detail": "token=never-print"}]},
        )

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(AppleVendorError) as raised:
            await adapter.submit(target=_target(), artifact_id="build-999")

    assert raised.value.code == "APPLE_API_REJECTED"
    assert "never-print" not in str(raised.value)


@pytest.mark.asyncio
async def test_queries_configured_version_review_status(p256_private_key: str) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "type": "appStoreVersions",
                    "id": "version-456",
                    "attributes": {"appStoreState": "IN_REVIEW"},
                }
            },
        )

    adapter, http = _adapter(p256_private_key, httpx.MockTransport(handler))
    async with http:
        status = await adapter.review_status(target=_target())

    assert status is ReviewStatus.IN_REVIEW
    assert requests[0].url.path == "/v1/appStoreVersions/version-456"
    assert dict(requests[0].url.params) == {"fields[appStoreVersions]": "appStoreState"}
