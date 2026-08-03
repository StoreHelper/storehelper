from __future__ import annotations

import json

import httpx
import pytest

from storehelper.credentials.models import HuaweiServiceAccount
from storehelper.stores.errors import ArtifactStillProcessingError
from storehelper.stores.harmonyos.adapter import HarmonyOSAdapter
from storehelper.stores.harmonyos.client import HarmonyOSClient
from storehelper.stores.huawei.auth import HuaweiAuth
from storehelper.stores.huawei.errors import HuaweiVendorError
from storehelper.stores.models import ProcessingState, ReviewStatus, StoreName, StoreTarget


def _target() -> StoreTarget:
    return StoreTarget(
        store=StoreName.HARMONYOS,
        label="Huawei AppGallery (HarmonyOS)",
        app_id="100000002",
        package_name="com.example.wallet.harmony",
        credential_profile="company",
        language="zh-CN",
    )


def _adapter(
    rsa_private_key: str,
    handler: httpx.AsyncBaseTransport,
) -> tuple[HarmonyOSAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler)
    auth = HuaweiAuth(
        HuaweiServiceAccount(
            key_id="key-1",
            sub_account="sub-1",
            private_key=rsa_private_key,
        )
    )
    return HarmonyOSAdapter(HarmonyOSClient(auth=auth, http=http)), http


@pytest.mark.asyncio
async def test_processing_notes_submit_and_status_contract(
    rsa_private_key: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/api/publish/v2/app-package-info"):
            return httpx.Response(
                200,
                json={"ret": {"code": 0}, "packageInfo": {"versionName": "1.2.3"}},
            )
        if request.url.path.endswith("/app-info"):
            return httpx.Response(
                200,
                json={"ret": {"code": 0}, "appInfo": {"releaseState": 4}},
            )
        return httpx.Response(200, json={"ret": {"code": 0}})

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        processing = await adapter.processing_status(
            target=_target(),
            artifact_id="package-42",
        )
        await adapter.prepare_release(
            target=_target(),
            artifact_id="package-42",
            release_notes="修复已知问题",
        )
        submission_id = await adapter.submit(target=_target(), artifact_id="package-42")
        review = await adapter.review_status(target=_target())

    assert processing.state is ProcessingState.READY
    assert submission_id == "100000002"
    assert review is ReviewStatus.IN_REVIEW
    processing_request, notes_request, submit_request, status_request = requests
    assert dict(processing_request.url.params) == {
        "packageId": "package-42",
        "appId": "100000002",
    }
    assert json.loads(await notes_request.aread()) == {
        "lang": "zh-CN",
        "newFeatures": "修复已知问题",
    }
    assert dict(notes_request.url.params) == {
        "appId": "100000002",
        "releaseType": "1",
        "releasePhase": "0",
    }
    assert json.loads(await submit_request.aread()) == {
        "releaseType": 1,
        "releasePhase": 0,
    }
    assert dict(submit_request.url.params) == {"appId": "100000002"}
    assert dict(status_request.url.params) == {
        "appId": "100000002",
        "releaseType": "1",
        "releasePhase": "0",
    }


@pytest.mark.asyncio
async def test_submit_processing_response_uses_generic_resumable_error(
    rsa_private_key: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ret": {"code": 204144727, "msg": "package is being compiled"}},
        )

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(ArtifactStillProcessingError) as raised:
            await adapter.submit(target=_target(), artifact_id="package-42")

    assert raised.value.resumable is True
    assert raised.value.vendor_code == "204144727"


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
async def test_maps_harmonyos_review_states(
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
async def test_release_notes_length_is_enforced_before_network(
    rsa_private_key: str,
    notes: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid notes reached Huawei")

    adapter, http = _adapter(rsa_private_key, httpx.MockTransport(handler))
    async with http:
        with pytest.raises(HuaweiVendorError):
            await adapter.update_release_notes(
                app_id="100000002",
                language="zh-CN",
                release_notes=notes,
            )
