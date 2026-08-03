from __future__ import annotations

import json

import httpx
import pytest

from storehelper.credentials.models import HuaweiServiceAccount
from storehelper.stores.errors import ArtifactStillProcessingError
from storehelper.stores.huawei.adapter import HuaweiAndroidAdapter, ReviewStatus
from storehelper.stores.huawei.auth import HuaweiAuth
from storehelper.stores.huawei.client import HuaweiClient
from storehelper.stores.huawei.errors import HuaweiVendorError
from storehelper.stores.models import StoreName, StoreTarget


def _target() -> StoreTarget:
    return StoreTarget(
        store=StoreName.HUAWEI,
        label="Huawei AppGallery (Android)",
        app_id="123",
        package_name="com.example.app",
        credential_profile="company",
        language="zh-CN",
    )


def _adapter(
    rsa_private_key: str,
    handler: httpx.AsyncBaseTransport,
) -> tuple[HuaweiAndroidAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler)
    auth = HuaweiAuth(
        HuaweiServiceAccount(
            key_id="key-1",
            sub_account="sub-1",
            private_key=rsa_private_key,
        )
    )
    return HuaweiAndroidAdapter(HuaweiClient(auth=auth, http=http)), http


@pytest.mark.asyncio
async def test_release_notes_submit_and_review_status_contract(
    rsa_private_key: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/app-language-info"):
            return httpx.Response(200, json={"ret": {"code": 0}})
        if request.url.path.endswith("/app-submit"):
            return httpx.Response(200, json={"ret": {"code": 0}})
        return httpx.Response(
            200,
            json={"ret": {"code": 0}, "appInfo": {"releaseState": 4}},
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        await adapter.prepare_release(
            target=_target(),
            artifact_id="42",
            release_notes="修复已知问题",
        )
        submission_id = await adapter.submit(target=_target(), artifact_id="42")
        status = await adapter.review_status(target=_target())

    assert submission_id == "123"
    assert status is ReviewStatus.IN_REVIEW
    notes_request, submit_request, _ = requests
    assert json.loads(await notes_request.aread()) == {
        "lang": "zh-CN",
        "newFeatures": "修复已知问题",
    }
    assert notes_request.url.params["releaseType"] == "1"
    assert submit_request.method == "POST"
    assert submit_request.url.params["releaseType"] == "1"


@pytest.mark.asyncio
async def test_submit_compiling_response_remains_resumable(
    rsa_private_key: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ret": {"code": 204144727, "msg": "package compiling"}},
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(ArtifactStillProcessingError) as raised:
            await adapter.submit(target=_target(), artifact_id="42")

    assert raised.value.resumable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("release_state", "expected"),
    [
        (0, ReviewStatus.APPROVED),
        (1, ReviewStatus.REJECTED),
        (4, ReviewStatus.IN_REVIEW),
        (5, ReviewStatus.IN_REVIEW),
        (7, ReviewStatus.PENDING_REVIEW),
        (8, ReviewStatus.REJECTED),
        (12, ReviewStatus.IN_REVIEW),
        (13, ReviewStatus.REJECTED),
        (999, ReviewStatus.UNKNOWN),
    ],
)
async def test_maps_review_states(
    rsa_private_key: str,
    release_state: int,
    expected: ReviewStatus,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ret": {"code": 0}, "appInfo": {"releaseState": release_state}},
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        assert await adapter.review_status(target=_target()) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("notes", ["", " ", "x" * 501])
async def test_release_notes_must_be_between_1_and_500_characters(
    rsa_private_key: str,
    notes: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid notes must not reach Huawei")

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(HuaweiVendorError):
            await adapter.update_release_notes(
                app_id="123",
                language="zh-CN",
                release_notes=notes,
            )
