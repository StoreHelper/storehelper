from __future__ import annotations

import asyncio
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from storehelper.artifacts.models import ArtifactInfo
from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode
from storehelper.domain.models import PublishRequest, PublishStage
from storehelper.publishing.service import Publisher
from storehelper.runs.models import RunState
from storehelper.runs.repository import RunRepository
from storehelper.stores.errors import ArtifactStillProcessingError, StoreVendorError
from storehelper.stores.huawei.package import validate_package
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

CompileState = ProcessingState
CompileStatus = ProcessingStatus


class FakeAdapter:
    def __init__(self, compile_states: list[CompileState] | None = None) -> None:
        self.compile_states = compile_states or [CompileState.READY]
        self.calls: list[str] = []
        self.submit_compiling_once = False
        self.submit_network_failure_once = False
        self.processing_artifact_id: str | None = None
        self.operation_id = "operation-7"

    async def verify(self, *, target: StoreTarget) -> VerifiedApplication:
        self.calls.append("verify")
        return VerifiedApplication(app_id=target.app_id, package_name=target.package_name)

    async def upload(self, *, target: StoreTarget, artifact: ArtifactInfo) -> UploadedArtifact:
        self.calls.append("upload")
        return UploadedArtifact(artifact_id="42", operation_id=self.operation_id)

    async def processing_status(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> ProcessingStatus:
        self.calls.append(f"compile:{operation_id}")
        state = (
            self.compile_states.pop(0) if len(self.compile_states) > 1 else self.compile_states[0]
        )
        return CompileStatus(
            state=state,
            reason="compile failed" if state is CompileState.FAILED else None,
            artifact_id=self.processing_artifact_id if state is CompileState.READY else None,
        )

    async def prepare_release(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
        release_notes: str | None,
    ) -> None:
        self.calls.append(f"notes:{operation_id}")

    async def submit(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> str:
        self.calls.append(f"submit:{operation_id}")
        if self.submit_compiling_once:
            self.submit_compiling_once = False
            raise ArtifactStillProcessingError(
                "HUAWEI_PACKAGE_COMPILING",
                "Package is still compiling.",
                vendor_code="204144727",
            )
        if self.submit_network_failure_once:
            self.submit_network_failure_once = False
            raise StoreVendorError(
                "STORE_NETWORK_ERROR",
                "Submission response was lost.",
                ExitCode.NETWORK,
                resumable=True,
            )
        return "submission-123"

    async def review_status(self, *, target: StoreTarget) -> ReviewStatus:
        self.calls.append("status")
        return ReviewStatus.IN_REVIEW


class AtomicFakeAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.atomic_failure: StoreHelperError | None = None
        self.cancel_atomic = False

    async def publish_atomic(
        self,
        *,
        target: StoreTarget,
        artifact: ArtifactInfo,
        release_notes: str | None,
    ) -> str:
        self.calls.append(f"atomic:{artifact.sha256}:{release_notes}")
        if self.cancel_atomic:
            raise asyncio.CancelledError
        if self.atomic_failure is not None:
            raise self.atomic_failure
        return target.package_name


class StagedFakeAdapter(FakeAdapter):
    def __init__(self, repository: RunRepository) -> None:
        super().__init__()
        self.repository = repository
        self.stage_failure: StoreHelperError | None = None
        self.commit_failure: StoreHelperError | None = None
        self.cancel_stage = False
        self.cancel_commit = False

    async def stage_submission(
        self,
        *,
        target: StoreTarget,
        artifact: ArtifactInfo,
        release_notes: str | None,
    ) -> None:
        self.calls.append(f"stage:{artifact.sha256}:{release_notes}")
        if self.cancel_stage:
            raise asyncio.CancelledError
        if self.stage_failure is not None:
            raise self.stage_failure

    async def commit_staged_submission(self, *, target: StoreTarget) -> str:
        state = self.repository.list()[0].state
        self.calls.append(f"commit:{state.value}")
        if self.cancel_commit:
            raise asyncio.CancelledError
        if self.commit_failure is not None:
            raise self.commit_failure
        return target.package_name


def _package(tmp_path: Path) -> Path:
    path = tmp_path / "release.apk"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
    return path


def _target(
    app_id: str = "123",
    *,
    track: str | None = None,
    release_status: str | None = None,
) -> StoreTarget:
    return StoreTarget(
        store=StoreName.HUAWEI,
        label="Huawei AppGallery (Android)",
        app_id=app_id,
        package_name="com.example.app",
        credential_profile="default",
        language="zh-CN",
        track=track,
        release_status=release_status,
    )


def _capabilities(
    *,
    processing: bool = True,
    atomic_submission: bool = False,
    staged_submission: bool = False,
    supports_no_submit: bool = True,
) -> StoreCapabilities:
    return StoreCapabilities(
        credential_kind=CredentialKind.HUAWEI_SERVICE_ACCOUNT,
        artifact_suffixes=(".apk", ".aab"),
        requires_processing_poll=processing,
        requires_release_notes=True,
        supports_review_status=True,
        atomic_submission=atomic_submission,
        staged_submission=staged_submission,
        supports_no_submit=supports_no_submit,
    )


def _publisher(
    *,
    adapter: FakeAdapter,
    repository: RunRepository,
    app_id: str = "123",
    processing: bool = True,
    atomic_submission: bool = False,
    staged_submission: bool = False,
    supports_no_submit: bool = True,
    track: str | None = None,
    release_status: str | None = None,
    **kwargs: object,
) -> Publisher:
    return Publisher(
        adapter=adapter,
        repository=repository,
        target=_target(app_id, track=track, release_status=release_status),
        validator=validate_package,
        capabilities=_capabilities(
            processing=processing,
            atomic_submission=atomic_submission,
            staged_submission=staged_submission,
            supports_no_submit=supports_no_submit,
        ),
        **kwargs,
    )


def _request(path: Path, **updates: object) -> PublishRequest:
    values: dict[str, object] = {
        "app_alias": "demo",
        "file": path,
        "release_notes": "Fixes",
        "submit": True,
        "confirmed": True,
        "poll_interval_seconds": 5,
        "wait_timeout_seconds": 30,
    }
    values.update(updates)
    return PublishRequest.model_validate(values)


@pytest.mark.asyncio
async def test_publish_uploads_waits_updates_and_submits(tmp_path: Path) -> None:
    adapter = FakeAdapter([CompileState.PROCESSING, CompileState.READY])
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    publisher = _publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
        sleeper=sleeper,
    )

    result = await publisher.publish(_request(_package(tmp_path)))

    assert result.ok is True
    assert result.stage is PublishStage.SUBMITTED
    assert adapter.calls == [
        "verify",
        "upload",
        "compile:operation-7",
        "compile:operation-7",
        "notes:operation-7",
        "submit:operation-7",
    ]
    assert sleeps == [5]
    receipt = publisher.repository.get(result.run_id or "")
    assert receipt.state is RunState.COMPLETED
    assert receipt.submission_id == "submission-123"
    assert receipt.operation_id == "operation-7"


@pytest.mark.asyncio
async def test_processing_ready_atomically_replaces_upload_handle(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    adapter.processing_artifact_id = "apple-build-99"
    publisher = _publisher(adapter=adapter, repository=RunRepository(tmp_path / "runs"))

    result = await publisher.publish(_request(_package(tmp_path)))

    receipt = publisher.repository.get(result.run_id or "")
    assert receipt.artifact_id == "apple-build-99"
    assert receipt.submission_id == "submission-123"


@pytest.mark.asyncio
async def test_dry_run_has_no_network_calls(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    publisher = _publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
    )

    result = await publisher.publish(_request(_package(tmp_path), dry_run=True))

    assert result.ok is True
    assert result.stage is PublishStage.COMPLETED
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_dry_run_is_not_blocked_by_existing_resumable_run(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "runs")
    package = _package(tmp_path)
    first_adapter = FakeAdapter([CompileState.PROCESSING])
    ticks = iter([0.0, 0.0, 5.0])

    async def sleeper(seconds: float) -> None:
        return None

    first = _publisher(
        adapter=first_adapter,
        repository=repo,
        clock=lambda: next(ticks),
        sleeper=sleeper,
    )
    timed_out = await first.publish(_request(package, wait_timeout_seconds=5))
    assert timed_out.resumable
    dry_adapter = FakeAdapter()
    dry = _publisher(adapter=dry_adapter, repository=repo)

    result = await dry.publish(_request(package, dry_run=True))

    assert result.ok is True
    assert result.stage is PublishStage.COMPLETED
    assert dry_adapter.calls == []


@pytest.mark.asyncio
async def test_no_submit_stops_after_package_ready(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    publisher = _publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
    )

    result = await publisher.publish(
        _request(_package(tmp_path), submit=False, release_notes=None, confirmed=False)
    )

    assert result.ok is True
    assert adapter.calls == ["verify", "upload", "compile:operation-7"]
    assert publisher.repository.get(result.run_id or "").state is RunState.COMPLETED


@pytest.mark.asyncio
async def test_store_without_processing_poll_skips_processing_call(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    publisher = _publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
        processing=False,
    )

    result = await publisher.publish(
        _request(_package(tmp_path), submit=False, release_notes=None, confirmed=False)
    )

    assert result.ok is True
    assert adapter.calls == ["verify", "upload"]


@pytest.mark.asyncio
async def test_timeout_is_resumable_without_reupload(tmp_path: Path) -> None:
    adapter = FakeAdapter([CompileState.PROCESSING])
    ticks = iter([0.0, 0.0, 5.0, 10.0])

    async def sleeper(seconds: float) -> None:
        return None

    publisher = _publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
        clock=lambda: next(ticks),
        sleeper=sleeper,
    )
    request = _request(_package(tmp_path), wait_timeout_seconds=5)

    result = await publisher.publish(request)

    assert result.ok is False
    assert result.resumable is True
    assert result.stage is PublishStage.TIMED_OUT
    assert result.next_action is not None
    assert "storehelper resume" in result.next_action.command
    assert publisher.repository.get(result.run_id or "").artifact_id == "42"


@pytest.mark.asyncio
async def test_duplicate_returns_existing_resume_action(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "runs")
    adapter = FakeAdapter([CompileState.PROCESSING])
    publisher = _publisher(adapter=adapter, repository=repo)
    package = _package(tmp_path)
    first = await publisher.publish(
        _request(package, wait_timeout_seconds=5),
    )
    assert first.resumable
    adapter.calls.clear()

    second = await publisher.publish(_request(package))

    assert second.ok is False
    assert second.run_id == first.run_id
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_resume_from_timeout_starts_at_compile(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "runs")
    adapter = FakeAdapter([CompileState.PROCESSING])
    publisher = _publisher(adapter=adapter, repository=repo)
    timed_out = await publisher.publish(
        _request(_package(tmp_path), wait_timeout_seconds=5),
    )
    adapter.calls.clear()
    adapter.compile_states = [CompileState.READY]

    resumed = await publisher.resume(timed_out.run_id or "")

    assert resumed.ok is True
    assert resumed.stage is PublishStage.SUBMITTED
    assert adapter.calls == [
        "compile:operation-7",
        "notes:operation-7",
        "submit:operation-7",
    ]


@pytest.mark.asyncio
async def test_resume_rejects_receipt_for_different_configured_app(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "runs")
    receipt = repo.create(
        store="huawei",
        app_alias="demo",
        app_id="123",
        package_name="com.example.app",
        package_path="/build/release.apk",
        package_sha256="abc",
        logical_name="release.apk",
        language="zh-CN",
        release_notes="Fixes",
        submit=True,
    )
    publisher = _publisher(
        adapter=FakeAdapter(),
        repository=repo,
        app_id="999",
    )

    with pytest.raises(StoreHelperError) as raised:
        await publisher.resume(receipt.run_id)

    assert raised.value.code == "RUN_APP_MISMATCH"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_track", "configured_status"),
    [("production", "draft"), ("internal", "completed")],
)
async def test_resume_rejects_changed_track_or_release_status_before_network(
    tmp_path: Path,
    configured_track: str,
    configured_status: str,
) -> None:
    repo = RunRepository(tmp_path / "runs")
    receipt = repo.create(
        store="huawei",
        app_alias="demo",
        app_id="123",
        package_name="com.example.app",
        package_path="/build/release.apk",
        package_sha256="abc",
        logical_name="release.apk",
        track="internal",
        release_status="draft",
        language="zh-CN",
        release_notes="Fixes",
        submit=True,
    )
    adapter = FakeAdapter()
    publisher = _publisher(
        adapter=adapter,
        repository=repo,
        track=configured_track,
        release_status=configured_status,
    )

    with pytest.raises(StoreHelperError) as raised:
        await publisher.resume(receipt.run_id)

    assert raised.value.code == "RUN_APP_MISMATCH"
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_resume_rejects_changed_local_package_before_network(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "runs")
    package = _package(tmp_path)
    artifact = validate_package(package)
    receipt = repo.create(
        store="huawei",
        app_alias="demo",
        app_id="123",
        package_name="com.example.app",
        package_path=str(package),
        package_sha256=artifact.sha256,
        logical_name=artifact.logical_name,
        language="zh-CN",
        release_notes="Fixes",
        submit=True,
    ).model_copy(update={"state": RunState.TIMED_OUT, "artifact_id": "42"})
    repo.save(receipt)
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr("changed.txt", b"different artifact")
    adapter = FakeAdapter()
    publisher = _publisher(adapter=adapter, repository=repo)

    with pytest.raises(StoreHelperError) as raised:
        await publisher.resume(receipt.run_id)

    assert raised.value.code == "PACKAGE_CHANGED"
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_resume_migrates_legacy_huawei_receipt_without_reupload(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    package = _package(tmp_path)
    artifact = validate_package(package)
    run_id = "20260803T100000Z-legacy01"
    now = datetime.now(UTC).isoformat()
    receipt_path = runs / f"{run_id}.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "created_at": now,
                "updated_at": now,
                "store": "huawei",
                "state": "package_compiling",
                "app_alias": "demo",
                "app_id": "123",
                "package_name": "com.example.app",
                "package_path": str(package),
                "package_sha256": artifact.sha256,
                "logical_name": artifact.logical_name,
                "pkg_version": "42",
                "language": "zh-CN",
                "release_notes": "Fixes",
                "submit": True,
            }
        ),
        encoding="utf-8",
    )
    adapter = FakeAdapter()
    publisher = _publisher(adapter=adapter, repository=RunRepository(runs))

    result = await publisher.resume(run_id)

    assert result.stage is PublishStage.SUBMITTED
    assert adapter.calls == ["compile:None", "notes:None", "submit:None"]
    rewritten = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert rewritten["schema_version"] == 5
    assert rewritten["artifact_id"] == "42"
    assert "pkg_version" not in rewritten


@pytest.mark.asyncio
async def test_status_uses_normalized_adapter_result(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    publisher = _publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
    )

    result = await publisher.status()

    assert result.ok is True
    assert result.message.endswith("in_review")


@pytest.mark.asyncio
async def test_submit_compiling_rechecks_compile_state_then_retries(tmp_path: Path) -> None:
    adapter = FakeAdapter([CompileState.READY])
    adapter.submit_compiling_once = True
    publisher = _publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
    )

    result = await publisher.publish(_request(_package(tmp_path)))

    assert result.ok is True
    assert adapter.calls == [
        "verify",
        "upload",
        "compile:operation-7",
        "notes:operation-7",
        "submit:operation-7",
        "compile:operation-7",
        "submit:operation-7",
    ]


@pytest.mark.asyncio
async def test_submission_network_failure_preserves_metadata_stage_for_resume(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter()
    adapter.processing_artifact_id = "processed-build-id"
    adapter.submit_network_failure_once = True
    publisher = _publisher(adapter=adapter, repository=RunRepository(tmp_path / "runs"))

    interrupted = await publisher.publish(_request(_package(tmp_path)))

    receipt = publisher.repository.get(interrupted.run_id or "")
    assert interrupted.stage is PublishStage.TIMED_OUT
    assert receipt.state is RunState.METADATA_UPDATED
    adapter.calls.clear()

    resumed = await publisher.resume(receipt.run_id)

    assert resumed.stage is PublishStage.SUBMITTED
    assert adapter.calls == ["submit:operation-7"]


@pytest.mark.asyncio
async def test_atomic_store_persists_started_then_completes_without_multistep_calls(
    tmp_path: Path,
) -> None:
    adapter = AtomicFakeAdapter()
    publisher = _publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
        processing=False,
        atomic_submission=True,
        supports_no_submit=False,
    )
    package = _package(tmp_path)
    artifact = validate_package(package)

    result = await publisher.publish(_request(package))

    assert result.stage is PublishStage.SUBMITTED
    assert adapter.calls == ["verify", f"atomic:{artifact.sha256}:Fixes"]
    receipt = publisher.repository.get(result.run_id or "")
    assert receipt.state is RunState.COMPLETED
    assert receipt.artifact_id == artifact.sha256
    assert receipt.submission_id == "com.example.app"


@pytest.mark.asyncio
async def test_atomic_no_submit_is_rejected_before_validation_or_network(tmp_path: Path) -> None:
    adapter = AtomicFakeAdapter()
    repository = RunRepository(tmp_path / "runs")
    publisher = _publisher(
        adapter=adapter,
        repository=repository,
        processing=False,
        atomic_submission=True,
        supports_no_submit=False,
    )

    with pytest.raises(StoreHelperError) as raised:
        await publisher.publish(
            _request(
                tmp_path / "missing.apk",
                submit=False,
                confirmed=False,
                release_notes=None,
            )
        )

    assert raised.value.code == "NO_SUBMIT_UNSUPPORTED"
    assert adapter.calls == []
    assert repository.list() == []


@pytest.mark.asyncio
async def test_atomic_response_loss_is_uncertain_nonresumable_and_blocks_duplicate(
    tmp_path: Path,
) -> None:
    adapter = AtomicFakeAdapter()
    adapter.atomic_failure = StoreVendorError(
        "ATOMIC_NETWORK_ERROR",
        "The submission response was lost.",
        ExitCode.NETWORK,
        resumable=False,
    )
    repository = RunRepository(tmp_path / "runs")
    publisher = _publisher(
        adapter=adapter,
        repository=repository,
        processing=False,
        atomic_submission=True,
        supports_no_submit=False,
    )
    package = _package(tmp_path)

    with pytest.raises(StoreHelperError) as raised:
        await publisher.publish(_request(package))

    assert raised.value.code == "ATOMIC_NETWORK_ERROR"
    uncertain = repository.list()[0]
    assert uncertain.state is RunState.SUBMISSION_UNCERTAIN
    assert uncertain.resumable is False
    adapter.calls.clear()

    duplicate = await publisher.publish(_request(package))

    assert duplicate.ok is False
    assert duplicate.stage is PublishStage.INTERRUPTED
    assert duplicate.resumable is False
    assert duplicate.run_id == uncertain.run_id
    assert duplicate.next_action is None
    assert "inspect" in duplicate.message.lower()
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_atomic_cancellation_after_start_is_uncertain_not_resumable(
    tmp_path: Path,
) -> None:
    adapter = AtomicFakeAdapter()
    adapter.cancel_atomic = True
    publisher = _publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
        processing=False,
        atomic_submission=True,
        supports_no_submit=False,
    )

    result = await publisher.publish(_request(_package(tmp_path)))

    assert result.stage is PublishStage.INTERRUPTED
    assert result.resumable is False
    receipt = publisher.repository.get(result.run_id or "")
    assert receipt.state is RunState.SUBMISSION_UNCERTAIN
    with pytest.raises(StoreHelperError) as raised:
        await publisher.resume(receipt.run_id)
    assert raised.value.code == "ATOMIC_RUN_NOT_RESUMABLE"


@pytest.mark.asyncio
async def test_atomic_started_receipt_from_hard_crash_blocks_retry(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    package = _package(tmp_path)
    artifact = validate_package(package)
    receipt = repository.create(
        store="huawei",
        app_alias="demo",
        app_id="123",
        package_name="com.example.app",
        package_path=str(package),
        package_sha256=artifact.sha256,
        logical_name=artifact.logical_name,
        language="zh-CN",
        release_notes="Fixes",
        submit=True,
    ).model_copy(update={"state": RunState.SUBMISSION_STARTED})
    repository.save(receipt)
    adapter = AtomicFakeAdapter()
    publisher = _publisher(
        adapter=adapter,
        repository=repository,
        processing=False,
        atomic_submission=True,
        supports_no_submit=False,
    )

    blocked = await publisher.publish(_request(package))

    assert blocked.ok is False
    assert blocked.run_id == receipt.run_id
    assert blocked.resumable is False
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_staged_store_persists_started_only_after_staging_then_commits(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    adapter = StagedFakeAdapter(repository)
    publisher = _publisher(
        adapter=adapter,
        repository=repository,
        processing=False,
        atomic_submission=True,
        staged_submission=True,
        supports_no_submit=False,
    )
    package = _package(tmp_path)
    artifact = validate_package(package)

    result = await publisher.publish(_request(package))

    assert result.stage is PublishStage.SUBMITTED
    assert adapter.calls == [
        "verify",
        f"stage:{artifact.sha256}:Fixes",
        "commit:submission_started",
    ]
    receipt = repository.get(result.run_id or "")
    assert receipt.state is RunState.COMPLETED
    assert receipt.artifact_id is None
    assert receipt.operation_id is None
    assert receipt.submission_id == "com.example.app"


@pytest.mark.asyncio
async def test_staged_failure_before_final_submission_is_failed_and_safe_to_retry(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    adapter = StagedFakeAdapter(repository)
    adapter.stage_failure = StoreVendorError(
        "OPPO_UPLOAD_FAILED",
        "The temporary upload failed.",
        ExitCode.NETWORK,
        resumable=False,
    )
    publisher = _publisher(
        adapter=adapter,
        repository=repository,
        processing=False,
        atomic_submission=True,
        staged_submission=True,
        supports_no_submit=False,
    )
    package = _package(tmp_path)

    with pytest.raises(StoreHelperError) as raised:
        await publisher.publish(_request(package))

    assert raised.value.code == "OPPO_UPLOAD_FAILED"
    assert repository.list()[0].state is RunState.FAILED
    adapter.stage_failure = None
    adapter.calls.clear()

    retried = await publisher.publish(_request(package))

    assert retried.ok is True
    assert len(repository.list()) == 2
    assert adapter.calls[-1] == "commit:submission_started"


@pytest.mark.asyncio
async def test_staged_cancellation_before_final_submission_is_failed_not_uncertain(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    adapter = StagedFakeAdapter(repository)
    adapter.cancel_stage = True
    publisher = _publisher(
        adapter=adapter,
        repository=repository,
        processing=False,
        atomic_submission=True,
        staged_submission=True,
        supports_no_submit=False,
    )

    result = await publisher.publish(_request(_package(tmp_path)))

    assert result.stage is PublishStage.INTERRUPTED
    assert result.resumable is False
    assert repository.get(result.run_id or "").state is RunState.FAILED


@pytest.mark.asyncio
async def test_staged_final_response_loss_is_uncertain_and_blocks_duplicate(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    adapter = StagedFakeAdapter(repository)
    adapter.commit_failure = StoreVendorError(
        "OPPO_SUBMISSION_UNCERTAIN",
        "The final response was lost.",
        ExitCode.NETWORK,
        resumable=False,
    )
    publisher = _publisher(
        adapter=adapter,
        repository=repository,
        processing=False,
        atomic_submission=True,
        staged_submission=True,
        supports_no_submit=False,
    )
    package = _package(tmp_path)

    with pytest.raises(StoreHelperError) as raised:
        await publisher.publish(_request(package))

    assert raised.value.code == "OPPO_SUBMISSION_UNCERTAIN"
    uncertain = repository.list()[0]
    assert uncertain.state is RunState.SUBMISSION_UNCERTAIN
    adapter.calls.clear()

    duplicate = await publisher.publish(_request(package))

    assert duplicate.ok is False
    assert duplicate.run_id == uncertain.run_id
    assert duplicate.resumable is False
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_staged_final_cancellation_is_uncertain_and_cannot_resume(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    adapter = StagedFakeAdapter(repository)
    adapter.cancel_commit = True
    publisher = _publisher(
        adapter=adapter,
        repository=repository,
        processing=False,
        atomic_submission=True,
        staged_submission=True,
        supports_no_submit=False,
    )

    result = await publisher.publish(_request(_package(tmp_path)))

    assert result.stage is PublishStage.INTERRUPTED
    receipt = repository.get(result.run_id or "")
    assert receipt.state is RunState.SUBMISSION_UNCERTAIN
    with pytest.raises(StoreHelperError) as raised:
        await publisher.resume(receipt.run_id)
    assert raised.value.code == "ATOMIC_RUN_NOT_RESUMABLE"
