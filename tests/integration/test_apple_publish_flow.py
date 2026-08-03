from __future__ import annotations

import asyncio
import plistlib
import zipfile
from pathlib import Path

import pytest

from storehelper.artifacts.models import AppleArtifactInfo, ArtifactInfo
from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode
from storehelper.domain.models import PublishRequest, PublishStage
from storehelper.publishing.service import Publisher
from storehelper.runs.models import RunState
from storehelper.runs.repository import RunRepository
from storehelper.stores.apple.errors import AppleVendorError
from storehelper.stores.apple.package import validate_ipa
from storehelper.stores.models import (
    CredentialKind,
    ProcessingState,
    ProcessingStatus,
    ReviewStatus,
    StoreCapabilities,
    StoreName,
    StoreTarget,
    UploadedArtifact,
    VerifiedApplication,
)


def _ipa(tmp_path: Path) -> Path:
    path = tmp_path / "Wallet.ipa"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "Payload/Wallet.app/Info.plist",
            plistlib.dumps(
                {
                    "CFBundleIdentifier": "com.example.wallet.ios",
                    "CFBundleShortVersionString": "1.2.3",
                    "CFBundleVersion": "42",
                }
            ),
        )
        archive.writestr("Payload/Wallet.app/Wallet", b"binary")
    return path


class AppleFlowAdapter:
    def __init__(self, processing: list[ProcessingState] | None = None) -> None:
        self.processing = processing or [ProcessingState.READY]
        self.calls: list[tuple[str, str | None]] = []
        self.cancel_upload_once = False
        self.fail_submission_once = False

    async def verify(self, *, target: StoreTarget) -> VerifiedApplication:
        self.calls.append(("verify", None))
        return VerifiedApplication(app_id=target.app_id, package_name=target.package_name)

    async def upload(self, *, target: StoreTarget, artifact: ArtifactInfo) -> UploadedArtifact:
        assert isinstance(artifact, AppleArtifactInfo)
        self.calls.append(("upload", None))
        if self.cancel_upload_once:
            self.cancel_upload_once = False
            raise asyncio.CancelledError
        return UploadedArtifact(artifact_id="upload-456")

    async def processing_status(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
    ) -> ProcessingStatus:
        self.calls.append(("processing", artifact_id))
        state = self.processing.pop(0) if len(self.processing) > 1 else self.processing[0]
        return ProcessingStatus(
            state=state,
            artifact_id="build-999" if state is ProcessingState.READY else None,
        )

    async def prepare_release(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        release_notes: str | None,
    ) -> None:
        self.calls.append(("prepare", artifact_id))

    async def submit(self, *, target: StoreTarget, artifact_id: str) -> str:
        self.calls.append(("submit", artifact_id))
        if self.fail_submission_once:
            self.fail_submission_once = False
            raise AppleVendorError(
                "APPLE_NETWORK_ERROR",
                "App Store Connect response was lost; token=known-secret-marker",
                ExitCode.NETWORK,
            )
        return "submission-1"

    async def review_status(self, *, target: StoreTarget) -> ReviewStatus:
        self.calls.append(("status", None))
        return ReviewStatus.IN_REVIEW


def _publisher(
    tmp_path: Path,
    adapter: AppleFlowAdapter,
    **kwargs: object,
) -> Publisher:
    return Publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
        target=StoreTarget(
            store=StoreName.APPLE,
            label="Apple App Store",
            app_id="app-123",
            package_name="com.example.wallet.ios",
            credential_profile="apple-release",
            language="zh-Hans",
            release_id="version-456",
            platform="IOS",
        ),
        validator=validate_ipa,
        capabilities=StoreCapabilities(
            credential_kind=CredentialKind.APPLE_API_KEY,
            artifact_suffixes=(".ipa",),
            requires_processing_poll=True,
            requires_release_notes=False,
            supports_review_status=True,
        ),
        **kwargs,
    )


def _request(path: Path, **updates: object) -> PublishRequest:
    values: dict[str, object] = {
        "app_alias": "wallet",
        "store": StoreName.APPLE,
        "file": path,
        "release_notes": None,
        "submit": True,
        "confirmed": True,
        "poll_interval_seconds": 5,
        "wait_timeout_seconds": 5,
    }
    values.update(updates)
    return PublishRequest.model_validate(values)


@pytest.mark.asyncio
async def test_valid_ipa_full_submit_and_no_submit_are_stateful(tmp_path: Path) -> None:
    full_adapter = AppleFlowAdapter()
    full = _publisher(tmp_path / "full", full_adapter)

    submitted = await full.publish(_request(_ipa(tmp_path / "full")))

    assert submitted.stage is PublishStage.SUBMITTED
    receipt = full.repository.get(submitted.run_id or "")
    assert receipt.artifact_id == "build-999"
    assert receipt.submission_id == "submission-1"
    assert receipt.release_id == "version-456"
    no_submit_adapter = AppleFlowAdapter()
    no_submit = _publisher(tmp_path / "no-submit", no_submit_adapter)
    uploaded = await no_submit.publish(
        _request(
            _ipa(tmp_path / "no-submit"),
            submit=False,
            confirmed=False,
        )
    )
    assert uploaded.stage is PublishStage.PACKAGE_READY
    assert [name for name, _ in no_submit_adapter.calls] == [
        "verify",
        "upload",
        "processing",
    ]


@pytest.mark.asyncio
async def test_dry_run_has_zero_adapter_io(tmp_path: Path) -> None:
    adapter = AppleFlowAdapter()
    publisher = _publisher(tmp_path, adapter)

    result = await publisher.publish(
        _request(_ipa(tmp_path), dry_run=True, submit=False, confirmed=False)
    )

    assert result.stage is PublishStage.COMPLETED
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_processing_timeout_resumes_without_reupload(tmp_path: Path) -> None:
    adapter = AppleFlowAdapter([ProcessingState.PROCESSING])
    ticks = iter([0.0, 0.0, 5.0, 6.0])

    async def no_sleep(seconds: float) -> None:
        return None

    publisher = _publisher(
        tmp_path,
        adapter,
        clock=lambda: next(ticks),
        sleeper=no_sleep,
    )
    result = await publisher.publish(_request(_ipa(tmp_path)))
    assert result.stage is PublishStage.TIMED_OUT
    adapter.calls.clear()
    adapter.processing = [ProcessingState.READY]

    resumed = await publisher.resume(result.run_id or "")

    assert resumed.stage is PublishStage.SUBMITTED
    assert adapter.calls == [
        ("processing", "upload-456"),
        ("prepare", "build-999"),
        ("submit", "build-999"),
    ]


@pytest.mark.asyncio
async def test_transfer_interruption_resumes_from_verified_app_for_exact_reconciliation(
    tmp_path: Path,
) -> None:
    adapter = AppleFlowAdapter()
    adapter.cancel_upload_once = True
    publisher = _publisher(tmp_path, adapter)

    interrupted = await publisher.publish(_request(_ipa(tmp_path)))

    assert interrupted.stage is PublishStage.INTERRUPTED
    assert interrupted.resumable is True
    receipt = publisher.repository.get(interrupted.run_id or "")
    assert receipt.state is RunState.APP_VERIFIED
    adapter.calls.clear()

    resumed = await publisher.resume(receipt.run_id)

    assert resumed.stage is PublishStage.SUBMITTED
    assert adapter.calls[0] == ("upload", None)


@pytest.mark.asyncio
async def test_submission_response_loss_resumes_at_reconciliation_without_repoll(
    tmp_path: Path,
) -> None:
    adapter = AppleFlowAdapter()
    adapter.fail_submission_once = True
    publisher = _publisher(tmp_path, adapter)

    interrupted = await publisher.publish(_request(_ipa(tmp_path)))

    assert interrupted.stage is PublishStage.TIMED_OUT
    assert interrupted.resumable is True
    assert "known-secret-marker" not in interrupted.message
    receipt = publisher.repository.get(interrupted.run_id or "")
    assert receipt.state is RunState.METADATA_UPDATED
    assert "known-secret-marker" not in (
        publisher.repository.root / f"{receipt.run_id}.json"
    ).read_text(encoding="utf-8")
    adapter.calls.clear()

    resumed = await publisher.resume(receipt.run_id)

    assert resumed.stage is PublishStage.SUBMITTED
    assert adapter.calls == [("submit", "build-999")]


@pytest.mark.asyncio
async def test_changed_ipa_is_rejected_before_resume_adapter_io(tmp_path: Path) -> None:
    adapter = AppleFlowAdapter([ProcessingState.PROCESSING])
    ticks = iter([0.0, 0.0, 5.0])

    async def no_sleep(seconds: float) -> None:
        return None

    publisher = _publisher(
        tmp_path,
        adapter,
        clock=lambda: next(ticks),
        sleeper=no_sleep,
    )
    ipa = _ipa(tmp_path)
    timed_out = await publisher.publish(_request(ipa))
    with zipfile.ZipFile(ipa, "a") as archive:
        archive.writestr("Payload/Wallet.app/changed", b"changed")
    adapter.calls.clear()

    with pytest.raises(StoreHelperError) as raised:
        await publisher.resume(timed_out.run_id or "")

    assert raised.value.code == "PACKAGE_CHANGED"
    assert adapter.calls == []
