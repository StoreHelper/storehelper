from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from storehelper.credentials.models import VivoApiCredential
from storehelper.domain.models import PublishRequest, PublishStage
from storehelper.output.renderers import render_error, render_result
from storehelper.publishing.service import Publisher, PublishingError
from storehelper.runs.models import RunState
from storehelper.runs.repository import RunRepository
from storehelper.stores.models import StoreName, StoreTarget
from storehelper.stores.registry import get_registration
from storehelper.stores.vivo.adapter import VivoAdapter
from storehelper.stores.vivo.auth import VivoAuth
from storehelper.stores.vivo.client import VivoClient
from storehelper.stores.vivo.errors import VivoVendorError


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
        store=StoreName.VIVO,
        label="vivo App Store",
        app_id="com.example.wallet",
        package_name="com.example.wallet",
        credential_profile="release",
        language="zh-CN",
        version_code=43,
    )


def _request(apk: Path, *, dry_run: bool = False) -> PublishRequest:
    return PublishRequest(
        app_alias="wallet",
        store=StoreName.VIVO,
        file=apk,
        release_notes="Security fixes",
        submit=not dry_run,
        dry_run=dry_run,
        confirmed=not dry_run,
        poll_interval_seconds=5,
        wait_timeout_seconds=5,
    )


class VivoBackend:
    def __init__(self) -> None:
        self.mode = "success"
        self.info_calls = 0
        self.upload_calls = 0
        self.final_calls = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers["content-type"].startswith("multipart/form-data"):
            self.upload_calls += 1
            await request.aread()
            if self.mode == "stage_loss":
                raise httpx.ReadError("known-vivo-secret", request=request)
            if self.mode == "stage_cancel":
                raise asyncio.CancelledError
            return httpx.Response(
                200,
                json={"code": 0, "data": {"serialnumber": "known-vivo-serial"}},
            )

        body = (await request.aread()).decode()
        form = {key: values[0] for key, values in parse_qs(body).items()}
        if form["method"] == "app.query.details":
            self.info_calls += 1
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "packageName": "com.example.wallet",
                        "versionCode": 42,
                        "status": 5,
                    },
                },
            )

        self.final_calls += 1
        if self.mode == "final_loss":
            raise httpx.ReadError("access_key=known-vivo-access", request=request)
        if self.mode == "final_cancel":
            raise asyncio.CancelledError
        if self.mode == "final_crash":
            raise HardCrash
        if self.mode == "final_vendor":
            return httpx.Response(
                200,
                json={
                    "code": 9,
                    "subCode": "B0302",
                    "msg": "known-vivo-secret known-vivo-serial",
                },
            )
        return httpx.Response(200, json={"code": 0, "data": {}})


def _publisher(
    tmp_path: Path,
    backend: VivoBackend,
) -> tuple[Publisher, httpx.AsyncClient]:
    credential = VivoApiCredential(
        access_key=SecretStr("known-vivo-access"),
        secret_key=SecretStr("known-vivo-secret"),
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    adapter = VivoAdapter(VivoClient(auth=VivoAuth(credential), http=http))
    registration = get_registration(StoreName.VIVO)
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
async def test_success_and_dry_run_keep_vivo_transient_values_out_of_state(
    tmp_path: Path,
) -> None:
    apk = _project(tmp_path)
    backend = VivoBackend()
    publisher, http = _publisher(tmp_path, backend)

    async with http:
        dry = await publisher.publish(_request(apk, dry_run=True))
        submitted = await publisher.publish(_request(apk))

    assert dry.stage is PublishStage.COMPLETED
    assert submitted.stage is PublishStage.SUBMITTED
    assert backend.info_calls == 2
    assert backend.upload_calls == backend.final_calls == 1
    output = io.StringIO()
    render_result(submitted, output="json", stdout=output)
    receipt = (publisher.repository.root / f"{submitted.run_id}.json").read_text(encoding="utf-8")
    for value in (
        "known-vivo-access",
        "known-vivo-secret",
        "known-vivo-serial",
    ):
        assert value not in output.getvalue()
        assert value not in receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stage_loss", "stage_cancel"])
async def test_staging_failure_or_cancellation_is_safe_for_new_run(
    tmp_path: Path, mode: str
) -> None:
    apk = _project(tmp_path)
    backend = VivoBackend()
    backend.mode = mode
    publisher, http = _publisher(tmp_path, backend)

    async with http:
        if mode == "stage_loss":
            with pytest.raises(VivoVendorError) as raised:
                await publisher.publish(_request(apk))
            assert raised.value.code == "VIVO_NETWORK_ERROR"
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
async def test_final_loss_blocks_duplicate_status_reconciles_and_delete_allows_retry(
    tmp_path: Path,
) -> None:
    apk = _project(tmp_path)
    backend = VivoBackend()
    backend.mode = "final_loss"
    publisher, http = _publisher(tmp_path, backend)

    async with http:
        with pytest.raises(VivoVendorError) as raised:
            await publisher.publish(_request(apk))
        assert raised.value.code == "VIVO_SUBMISSION_UNCERTAIN"
        receipt = publisher.repository.list()[0]
        assert receipt.state is RunState.SUBMISSION_UNCERTAIN
        assert receipt.resumable is False
        error_output = io.StringIO()
        render_error(raised.value, output="json", stdout=error_output)
        assert "known-vivo-access" not in error_output.getvalue()
        calls = (backend.info_calls, backend.upload_calls, backend.final_calls)
        blocked = await publisher.publish(_request(apk))
        assert blocked.stage is PublishStage.INTERRUPTED
        assert calls == (backend.info_calls, backend.upload_calls, backend.final_calls)
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
    backend = VivoBackend()
    backend.mode = "final_cancel"
    publisher, http = _publisher(tmp_path, backend)

    async with http:
        result = await publisher.publish(_request(apk))

    assert result.stage is PublishStage.INTERRUPTED
    assert result.resumable is False
    assert publisher.repository.get(result.run_id or "").state is RunState.SUBMISSION_UNCERTAIN
    assert backend.final_calls == 1


@pytest.mark.asyncio
async def test_hard_crash_leaves_started_receipt_and_blocks_same_artifact(tmp_path: Path) -> None:
    apk = _project(tmp_path)
    backend = VivoBackend()
    backend.mode = "final_crash"
    publisher, http = _publisher(tmp_path, backend)

    async with http:
        with pytest.raises(HardCrash):
            await publisher.publish(_request(apk))
        receipt = publisher.repository.list()[0]
        assert receipt.state is RunState.SUBMISSION_STARTED
        calls = (backend.info_calls, backend.upload_calls, backend.final_calls)
        blocked = await publisher.publish(_request(apk))

    assert blocked.stage is PublishStage.INTERRUPTED
    assert blocked.resumable is False
    assert calls == (backend.info_calls, backend.upload_calls, backend.final_calls)


@pytest.mark.asyncio
async def test_deterministic_vendor_rejection_fails_without_uncertain_lock(
    tmp_path: Path,
) -> None:
    apk = _project(tmp_path)
    backend = VivoBackend()
    backend.mode = "final_vendor"
    publisher, http = _publisher(tmp_path, backend)

    async with http:
        with pytest.raises(VivoVendorError) as raised:
            await publisher.publish(_request(apk))
        assert raised.value.code == "VIVO_UPDATE_CONFLICT"
        assert "known-vivo-secret" not in str(raised.value)
        assert publisher.repository.list()[0].state is RunState.FAILED
        backend.mode = "success"
        retried = await publisher.publish(_request(apk))

    assert retried.stage is PublishStage.SUBMITTED
    assert backend.final_calls == 2
