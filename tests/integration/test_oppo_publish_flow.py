from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from storehelper.credentials.models import OppoApiCredential
from storehelper.domain.models import PublishRequest, PublishStage
from storehelper.output.renderers import render_error, render_result
from storehelper.publishing.service import Publisher, PublishingError
from storehelper.runs.models import RunState
from storehelper.runs.repository import RunRepository
from storehelper.stores.models import StoreName, StoreTarget
from storehelper.stores.oppo.adapter import OppoAdapter
from storehelper.stores.oppo.auth import OppoAuth
from storehelper.stores.oppo.client import OppoClient
from storehelper.stores.oppo.errors import OppoVendorError
from storehelper.stores.registry import get_registration


class HardCrash(BaseException):
    pass


def _project(tmp_path: Path) -> Path:
    apk = tmp_path / "wallet.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
    return apk


def _target() -> StoreTarget:
    return StoreTarget(
        store=StoreName.OPPO,
        label="OPPO Software Store",
        app_id="com.example.wallet",
        package_name="com.example.wallet",
        credential_profile="release",
        language="zh-CN",
        version_code=43,
    )


def _request(apk: Path, *, dry_run: bool = False) -> PublishRequest:
    return PublishRequest(
        app_alias="wallet",
        store=StoreName.OPPO,
        file=apk,
        release_notes="Security fixes",
        submit=not dry_run,
        dry_run=dry_run,
        confirmed=not dry_run,
        poll_interval_seconds=5,
        wait_timeout_seconds=5,
    )


class OppoBackend:
    def __init__(self) -> None:
        self.mode = "success"
        self.info_calls = 0
        self.allocation_calls = 0
        self.upload_calls = 0
        self.final_calls = 0

    @staticmethod
    def application() -> dict[str, object]:
        return {
            "pkg_name": "com.example.wallet",
            "version_code": "42",
            "audit_status": 111,
            "app_name": "Known Existing Wallet",
            "second_category_id": "463",
            "third_category_id": "6648",
            "summary": "Known summary",
            "detail_desc": "Known existing long description.",
            "privacy_source_url": "https://example.com/known-privacy",
            "icon_url": "https://cdn.example.com/known-icon.png",
            "pic_url": "https://cdn.example.com/known-screenshot.png",
            "age_level": "18",
            "adaptive_equipment": "4",
            "copyright_url": "https://cdn.example.com/known-copyright.pdf",
            "business_username": "Known Owner",
            "business_email": "known-owner@example.com",
            "business_mobile": "13800138000",
        }

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/resource/v1/app/info":
            self.info_calls += 1
            return httpx.Response(200, json={"errno": 0, "data": self.application()})
        if request.url.path == "/resource/v1/upload/get-upload-url":
            self.allocation_calls += 1
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {
                        "upload_url": "https://upload.oppomobile.com/known-upload-path",
                        "sign": "known-oppo-upload-sign",
                    },
                },
            )
        if request.url.host == "upload.oppomobile.com":
            self.upload_calls += 1
            await request.aread()
            if self.mode == "stage_loss":
                raise httpx.ReadError("known-oppo-upload-sign", request=request)
            if self.mode == "stage_cancel":
                raise asyncio.CancelledError
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {"url": "https://cdn.oppomobile.com/known-private-apk-url"},
                },
            )
        assert request.url.path == "/resource/v1/app/upd"
        self.final_calls += 1
        await request.aread()
        if self.mode == "final_loss":
            raise httpx.ReadError(
                "access_token=known-oppo-token",
                request=request,
            )
        if self.mode == "final_cancel":
            raise asyncio.CancelledError
        if self.mode == "final_crash":
            raise HardCrash
        if self.mode == "final_vendor":
            return httpx.Response(
                200,
                json={
                    "errno": 910006,
                    "data": {"message": "known-oppo-token known-private-apk-url"},
                },
            )
        return httpx.Response(200, json={"errno": 0, "data": {"task_id": "known-task"}})


def _publisher(
    tmp_path: Path,
    backend: OppoBackend,
) -> tuple[Publisher, httpx.AsyncClient]:
    credential = OppoApiCredential(
        client_id=SecretStr("known-oppo-client"),
        client_secret=SecretStr("known-oppo-secret"),
    )
    auth = OppoAuth(credential)
    auth.cache_token("known-oppo-token", expires_in=7200)
    http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    adapter = OppoAdapter(OppoClient(credential=credential, auth=auth, http=http))
    registration = get_registration(StoreName.OPPO)
    return (
        Publisher(
            adapter=adapter,
            repository=RunRepository(tmp_path / "runs"),
            target=_target(),
            validator=registration.validator,
            capabilities=registration.capabilities,
            target_validator=registration.target_validator,
        ),
        http,
    )


@pytest.mark.asyncio
async def test_success_and_dry_run_keep_all_oppo_transient_values_out_of_state(
    tmp_path: Path,
) -> None:
    apk = _project(tmp_path)
    backend = OppoBackend()
    publisher, http = _publisher(tmp_path, backend)

    async with http:
        dry = await publisher.publish(_request(apk, dry_run=True))
        submitted = await publisher.publish(_request(apk))

    assert dry.stage is PublishStage.COMPLETED
    assert submitted.stage is PublishStage.SUBMITTED
    assert backend.info_calls == 2
    assert backend.allocation_calls == backend.upload_calls == backend.final_calls == 1
    output = io.StringIO()
    render_result(submitted, output="json", stdout=output)
    receipt = (publisher.repository.root / f"{submitted.run_id}.json").read_text(encoding="utf-8")
    forbidden = (
        "known-oppo-client",
        "known-oppo-secret",
        "known-oppo-token",
        "known-oppo-upload-sign",
        "known-upload-path",
        "known-private-apk-url",
        "Known Existing Wallet",
        "Known existing long description",
        "known-icon.png",
        "known-screenshot.png",
        "known-copyright.pdf",
        "Known Owner",
        "known-owner@example.com",
        "known-task",
    )
    for value in forbidden:
        assert value not in output.getvalue()
        assert value not in receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stage_loss", "stage_cancel"])
async def test_staging_failure_or_cancellation_is_safe_for_a_new_run(
    tmp_path: Path,
    mode: str,
) -> None:
    apk = _project(tmp_path)
    backend = OppoBackend()
    backend.mode = mode
    publisher, http = _publisher(tmp_path, backend)

    async with http:
        if mode == "stage_loss":
            with pytest.raises(OppoVendorError) as raised:
                await publisher.publish(_request(apk))
            assert raised.value.code == "OPPO_NETWORK_ERROR"
        else:
            interrupted = await publisher.publish(_request(apk))
            assert interrupted.stage is PublishStage.INTERRUPTED
        assert publisher.repository.list()[0].state is RunState.FAILED
        backend.mode = "success"
        retried = await publisher.publish(_request(apk))

    assert retried.stage is PublishStage.SUBMITTED
    assert backend.upload_calls == 2
    assert backend.final_calls == 1


@pytest.mark.asyncio
async def test_final_response_loss_blocks_duplicate_status_reconciles_then_delete_allows_retry(
    tmp_path: Path,
) -> None:
    apk = _project(tmp_path)
    backend = OppoBackend()
    backend.mode = "final_loss"
    publisher, http = _publisher(tmp_path, backend)

    async with http:
        with pytest.raises(OppoVendorError) as raised:
            await publisher.publish(_request(apk))
        assert raised.value.code == "OPPO_SUBMISSION_UNCERTAIN"
        receipt = publisher.repository.list()[0]
        assert receipt.state is RunState.SUBMISSION_UNCERTAIN
        assert receipt.resumable is False
        error_output = io.StringIO()
        render_error(raised.value, output="json", stdout=error_output)
        assert "known-oppo-token" not in error_output.getvalue()
        calls = (
            backend.info_calls,
            backend.allocation_calls,
            backend.upload_calls,
            backend.final_calls,
        )
        blocked = await publisher.publish(_request(apk))
        assert blocked.stage is PublishStage.INTERRUPTED
        assert blocked.resumable is False
        assert calls == (
            backend.info_calls,
            backend.allocation_calls,
            backend.upload_calls,
            backend.final_calls,
        )
        with pytest.raises(PublishingError) as resume_error:
            await publisher.resume(receipt.run_id)
        assert resume_error.value.code == "ATOMIC_RUN_NOT_RESUMABLE"

        status = await publisher.status()
        assert "approved" in status.message
        assert publisher.repository.delete(receipt.run_id) is True
        backend.mode = "success"
        retried = await publisher.publish(_request(apk))

    assert retried.stage is PublishStage.SUBMITTED
    assert backend.final_calls == 2


@pytest.mark.asyncio
async def test_final_cancellation_is_uncertain_and_non_resumable(tmp_path: Path) -> None:
    apk = _project(tmp_path)
    backend = OppoBackend()
    backend.mode = "final_cancel"
    publisher, http = _publisher(tmp_path, backend)

    async with http:
        result = await publisher.publish(_request(apk))

    assert result.stage is PublishStage.INTERRUPTED
    assert result.resumable is False
    assert publisher.repository.get(result.run_id or "").state is RunState.SUBMISSION_UNCERTAIN
    assert backend.final_calls == 1


@pytest.mark.asyncio
async def test_hard_crash_leaves_started_receipt_and_blocks_same_artifact(
    tmp_path: Path,
) -> None:
    apk = _project(tmp_path)
    backend = OppoBackend()
    backend.mode = "final_crash"
    publisher, http = _publisher(tmp_path, backend)

    async with http:
        with pytest.raises(HardCrash):
            await publisher.publish(_request(apk))
        receipt = publisher.repository.list()[0]
        assert receipt.state is RunState.SUBMISSION_STARTED
        calls = (
            backend.info_calls,
            backend.allocation_calls,
            backend.upload_calls,
            backend.final_calls,
        )
        blocked = await publisher.publish(_request(apk))

    assert blocked.stage is PublishStage.INTERRUPTED
    assert blocked.resumable is False
    assert calls == (
        backend.info_calls,
        backend.allocation_calls,
        backend.upload_calls,
        backend.final_calls,
    )


@pytest.mark.asyncio
async def test_deterministic_final_rejection_fails_without_uncertain_lock(
    tmp_path: Path,
) -> None:
    apk = _project(tmp_path)
    backend = OppoBackend()
    backend.mode = "final_vendor"
    publisher, http = _publisher(tmp_path, backend)

    async with http:
        with pytest.raises(OppoVendorError) as raised:
            await publisher.publish(_request(apk))
        assert raised.value.code == "OPPO_VERSION_EXISTS"
        assert "known-oppo-token" not in str(raised.value)
        assert publisher.repository.list()[0].state is RunState.FAILED
        backend.mode = "success"
        retried = await publisher.publish(_request(apk))

    assert retried.stage is PublishStage.SUBMITTED
    assert backend.final_calls == 2
