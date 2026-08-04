from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import httpx
import pytest

from storehelper.credentials.models import HonorApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.honor.adapter import HonorAdapter
from storehelper.stores.honor.auth import HonorAuth
from storehelper.stores.honor.client import HonorClient
from storehelper.stores.honor.errors import HonorVendorError
from storehelper.stores.models import StoreName, StoreTarget

Sleeper = Callable[[float], Awaitable[None]]
SHA256 = "b" * 64
ARTIFACT_ID = f"987654321:{SHA256}"


def _target(*, version_code: int = 43, language: str = "zh-CN") -> StoreTarget:
    return StoreTarget(
        store=StoreName.HONOR,
        label="HONOR App Market",
        app_id="com.example.wallet",
        package_name="com.example.wallet",
        credential_profile="release",
        language=language,
        version_code=version_code,
    )


class SubmissionBackend:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bound = False
        self.notes = "旧版本说明"
        self.audit_result = 4
        self.current_version: int | None = 43
        self.release_id: str | None = "draft-43"
        self.fail_after: str | None = None
        self.reconcile_submission = True
        self.mutation_counts = {"bind": 0, "language": 0, "submit": 0}

    def detail(self) -> dict[str, object]:
        files: list[dict[str, object]] = []
        if self.bound:
            files.append(
                {
                    "fileType": 100,
                    "fileSha256": SHA256,
                    "fileUrl": "https://example.invalid/private.apk",
                }
            )
        return {
            "basicInfo": {
                "appId": 123456,
                "packageName": "com.example.wallet",
                "secretKey": "must-never-leak",
            },
            "languageInfo": [
                {
                    "languageId": "zh-CN",
                    "appName": "示例钱包",
                    "intro": "已有应用介绍",
                    "briefIntro": "已有简介",
                    "newFeature": self.notes,
                },
                {
                    "languageId": "en-US",
                    "appName": "Wallet",
                    "intro": "Existing description",
                    "briefIntro": "Existing summary",
                    "newFeature": "Old notes",
                },
            ],
            "fileInfo": files,
            "releaseInfo": {"versionCode": 42},
        }

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "iam.developer.honor.com":
            return httpx.Response(
                200,
                json={"access_token": "token", "expires_in": 3600, "token_type": "Bearer"},
            )
        if request.url.path.endswith("/get-app-id"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [{"appId": 123456, "packageName": "com.example.wallet"}],
                },
            )
        if request.url.path.endswith("/get-app-detail"):
            return httpx.Response(200, json={"code": 0, "data": self.detail()})
        if request.url.path.endswith("/get-app-current-release"):
            data = {
                "appId": 123456,
                "releaseId": self.release_id,
                "versionCode": self.current_version,
                "auditResult": self.audit_result,
            }
            return httpx.Response(200, json={"code": 0, "data": data})
        if request.url.path.endswith("/update-file-info"):
            self.mutation_counts["bind"] += 1
            assert dict(request.url.params) == {"appId": "123456"}
            assert json.loads(request.content) == {"bindingFileList": [{"objectId": 987654321}]}
            self.bound = True
            if self.fail_after == "bind":
                self.fail_after = None
                raise httpx.ReadError("private binding response", request=request)
            return httpx.Response(200, json={"code": 0, "msg": "ok"})
        if request.url.path.endswith("/update-language-info"):
            self.mutation_counts["language"] += 1
            assert dict(request.url.params) == {"appId": "123456"}
            assert json.loads(request.content) == {
                "languageInfoList": [
                    {
                        "languageId": "zh-CN",
                        "appName": "示例钱包",
                        "intro": "已有应用介绍",
                        "briefIntro": "已有简介",
                        "newFeature": "修复已知问题",
                    }
                ],
                "setAll": 0,
            }
            self.notes = "修复已知问题"
            if self.fail_after == "language":
                self.fail_after = None
                raise httpx.ReadError("private language response", request=request)
            return httpx.Response(200, json={"code": 0, "msg": "ok"})
        if request.url.path.endswith("/submit-audit"):
            self.mutation_counts["submit"] += 1
            assert dict(request.url.params) == {"appId": "123456"}
            assert json.loads(request.content) == {"forceUpdate": 0, "releaseType": 1}
            if self.reconcile_submission:
                self.audit_result = 0
                self.current_version = 43
                self.release_id = "review-43"
            if self.fail_after == "submit":
                raise httpx.ReadError("private submit response", request=request)
            return httpx.Response(200, json={"code": 0, "data": "review-43"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


def _adapter(
    backend: SubmissionBackend, *, sleeper: Sleeper | None = None
) -> tuple[HonorAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    auth = HonorAuth(
        HonorApiCredential(client_id="client", client_secret="secret"),
        http,
        clock=lambda: 1_700_000_000.0,
    )
    client = HonorClient(auth=auth, http=http, sleeper=lambda _: asyncio.sleep(0))
    kwargs = {} if sleeper is None else {"reconcile_sleeper": sleeper}
    return HonorAdapter(client, **kwargs), http


@pytest.mark.asyncio
async def test_honor_prepares_exact_delta_and_submits_once_then_reconciles() -> None:
    backend = SubmissionBackend()
    adapter, http = _adapter(backend)
    try:
        await adapter.prepare_release(
            target=_target(),
            artifact_id=ARTIFACT_ID,
            operation_id="123456",
            release_notes="修复已知问题",
        )
        release_id = await adapter.submit(
            target=_target(), artifact_id=ARTIFACT_ID, operation_id="123456"
        )
        replayed = await adapter.submit(
            target=_target(), artifact_id=ARTIFACT_ID, operation_id="123456"
        )
    finally:
        await http.aclose()

    assert release_id == "review-43"
    assert replayed == "review-43"
    assert backend.mutation_counts == {"bind": 1, "language": 1, "submit": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "already_bound, notes", [(True, "旧版本说明"), (False, "修复已知问题"), (True, "修复已知问题")]
)
async def test_honor_prepare_reconciles_completed_steps(already_bound: bool, notes: str) -> None:
    backend = SubmissionBackend()
    backend.bound = already_bound
    backend.notes = notes
    adapter, http = _adapter(backend)
    try:
        await adapter.prepare_release(
            target=_target(),
            artifact_id=ARTIFACT_ID,
            operation_id="123456",
            release_notes="修复已知问题",
        )
    finally:
        await http.aclose()

    assert backend.mutation_counts == {
        "bind": 0 if already_bound else 1,
        "language": 0 if notes == "修复已知问题" else 1,
        "submit": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["bind", "language"])
async def test_honor_prepare_resumes_after_ambiguous_mutation(stage: str) -> None:
    backend = SubmissionBackend()
    if stage == "language":
        backend.bound = True
    backend.fail_after = stage
    adapter, http = _adapter(backend)
    try:
        with pytest.raises(HonorVendorError) as raised:
            await adapter.prepare_release(
                target=_target(),
                artifact_id=ARTIFACT_ID,
                operation_id="123456",
                release_notes="修复已知问题",
            )
        await adapter.prepare_release(
            target=_target(),
            artifact_id=ARTIFACT_ID,
            operation_id="123456",
            release_notes="修复已知问题",
        )
    finally:
        await http.aclose()

    assert raised.value.resumable is True
    assert backend.mutation_counts[stage] == 1
    assert backend.bound is True
    assert backend.notes == "修复已知问题"


@pytest.mark.asyncio
async def test_honor_submit_reconciles_response_loss_without_duplicate_post() -> None:
    backend = SubmissionBackend()
    backend.bound = True
    backend.notes = "修复已知问题"
    backend.fail_after = "submit"
    adapter, http = _adapter(backend, sleeper=lambda _: asyncio.sleep(0))
    try:
        release_id = await adapter.submit(
            target=_target(), artifact_id=ARTIFACT_ID, operation_id="123456"
        )
    finally:
        await http.aclose()

    assert release_id == "review-43"
    assert backend.mutation_counts["submit"] == 1


@pytest.mark.asyncio
async def test_honor_submit_keeps_ambiguous_error_after_bounded_read_reconciliation() -> None:
    backend = SubmissionBackend()
    backend.bound = True
    backend.notes = "修复已知问题"
    backend.fail_after = "submit"
    backend.reconcile_submission = False
    reconciliation_delays: list[float] = []

    async def sleeper(delay: float) -> None:
        reconciliation_delays.append(delay)

    adapter, http = _adapter(backend, sleeper=sleeper)
    try:
        with pytest.raises(HonorVendorError) as raised:
            await adapter.submit(target=_target(), artifact_id=ARTIFACT_ID, operation_id="123456")
    finally:
        await http.aclose()

    assert raised.value.code == "HONOR_NETWORK_ERROR"
    assert raised.value.resumable is True
    assert backend.mutation_counts["submit"] == 1
    assert reconciliation_delays == [1.0, 2.0, 3.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "artifact_id, operation_id, notes, expected_code",
    [
        ("invalid", "123456", "修复已知问题", "HONOR_RECEIPT_INVALID"),
        (ARTIFACT_ID, None, "修复已知问题", "HONOR_RECEIPT_INVALID"),
        (ARTIFACT_ID, "0", "修复已知问题", "HONOR_RECEIPT_INVALID"),
        (ARTIFACT_ID, "999999", "修复已知问题", "HONOR_RECEIPT_MISMATCH"),
        (ARTIFACT_ID, "123456", None, "HONOR_RELEASE_NOTES_REQUIRED"),
        (ARTIFACT_ID, "123456", "", "HONOR_RELEASE_NOTES_REQUIRED"),
        (ARTIFACT_ID, "123456", "x" * 501, "HONOR_RELEASE_NOTES_INVALID"),
    ],
)
async def test_honor_prepare_rejects_invalid_context_or_notes(
    artifact_id: str,
    operation_id: str | None,
    notes: str | None,
    expected_code: str,
) -> None:
    backend = SubmissionBackend()
    adapter, http = _adapter(backend)
    try:
        with pytest.raises(HonorVendorError) as raised:
            await adapter.prepare_release(
                target=_target(),
                artifact_id=artifact_id,
                operation_id=operation_id,
                release_notes=notes,
            )
    finally:
        await http.aclose()

    assert raised.value.code == expected_code
    assert backend.mutation_counts == {"bind": 0, "language": 0, "submit": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "audit_result, version_code, release_id, expected_code",
    [
        (0, 44, "review-44", "HONOR_SUBMISSION_CONFLICT"),
        (3, 43, "other-43", "HONOR_SUBMISSION_STATE_UNKNOWN"),
        (4, 44, "draft-44", "HONOR_SUBMISSION_CONFLICT"),
        (4, None, "draft-empty", "HONOR_SUBMISSION_CONFLICT"),
    ],
)
async def test_honor_submit_fails_closed_on_conflicting_state(
    audit_result: int,
    version_code: int | None,
    release_id: str | None,
    expected_code: str,
) -> None:
    backend = SubmissionBackend()
    backend.audit_result = audit_result
    backend.current_version = version_code
    backend.release_id = release_id
    adapter, http = _adapter(backend)
    try:
        with pytest.raises(HonorVendorError) as raised:
            await adapter.submit(target=_target(), artifact_id=ARTIFACT_ID, operation_id="123456")
    finally:
        await http.aclose()

    assert raised.value.code == expected_code
    assert raised.value.exit_code is ExitCode.VENDOR_REJECTION
    assert backend.mutation_counts["submit"] == 0
