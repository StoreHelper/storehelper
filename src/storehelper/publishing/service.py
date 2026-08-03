"""Resumable publishing orchestration independent of CLI presentation."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from storehelper.artifacts.models import ArtifactInfo
from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode
from storehelper.domain.models import OperationResult, PublishRequest, PublishStage
from storehelper.runs.models import RunReceipt, RunState
from storehelper.runs.repository import RunRepository
from storehelper.stores.base import AtomicStoreAdapter, StoreAdapter
from storehelper.stores.errors import ArtifactStillProcessingError, StoreVendorError
from storehelper.stores.models import (
    ProcessingState,
    ProcessingStatus,
    StoreCapabilities,
    StoreTarget,
)

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]
ArtifactValidator = Callable[[Path], ArtifactInfo]
TargetValidator = Callable[[StoreTarget], None]

_STAGES = {
    RunState.CREATED: PublishStage.CREATED,
    RunState.VALIDATED: PublishStage.VALIDATED,
    RunState.APP_VERIFIED: PublishStage.APP_VERIFIED,
    RunState.PACKAGE_BOUND: PublishStage.PACKAGE_BOUND,
    RunState.PACKAGE_COMPILING: PublishStage.PACKAGE_COMPILING,
    RunState.PACKAGE_READY: PublishStage.PACKAGE_READY,
    RunState.METADATA_UPDATED: PublishStage.METADATA_UPDATED,
    RunState.SUBMISSION_STARTED: PublishStage.INTERRUPTED,
    RunState.SUBMISSION_UNCERTAIN: PublishStage.INTERRUPTED,
    RunState.SUBMITTED: PublishStage.SUBMITTED,
    RunState.COMPLETED: PublishStage.COMPLETED,
    RunState.TIMED_OUT: PublishStage.TIMED_OUT,
    RunState.INTERRUPTED: PublishStage.INTERRUPTED,
    RunState.FAILED: PublishStage.FAILED,
}


class PublishingError(StoreHelperError):
    pass


class Publisher:
    def __init__(
        self,
        *,
        adapter: StoreAdapter,
        repository: RunRepository,
        target: StoreTarget,
        validator: ArtifactValidator,
        capabilities: StoreCapabilities,
        target_validator: TargetValidator | None = None,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._adapter = adapter
        self.repository = repository
        self._target = target
        self._validator = validator
        self._capabilities = capabilities
        self._target_validator = target_validator
        self._clock = clock
        self._sleeper = sleeper

    def _transition(
        self,
        receipt: RunReceipt,
        state: RunState,
        **updates: object,
    ) -> RunReceipt:
        next_receipt = receipt.model_copy(
            update={"state": state, "updated_at": datetime.now(UTC), **updates}
        )
        self.repository.save(next_receipt)
        return next_receipt

    async def publish(self, request: PublishRequest) -> OperationResult:
        if request.store is not self._target.store:
            raise PublishingError(
                "STORE_TARGET_MISMATCH",
                "The publish request does not match the resolved store target.",
                ExitCode.USAGE,
            )
        if not request.submit and not request.dry_run and not self._capabilities.supports_no_submit:
            raise PublishingError(
                "NO_SUBMIT_UNSUPPORTED",
                f"{self._target.label} cannot upload without submitting for review; "
                "use --dry-run for local validation.",
                ExitCode.USAGE,
            )
        if request.submit and not request.dry_run:
            if not request.confirmed:
                raise PublishingError(
                    "PUBLISH_CONFIRMATION_REQUIRED",
                    "Full publishing requires explicit confirmation or --yes.",
                    ExitCode.USAGE,
                )
            notes = (request.release_notes or "").strip()
            if self._capabilities.requires_release_notes and not 1 <= len(notes) <= 500:
                raise PublishingError(
                    "RELEASE_NOTES_INVALID",
                    "Release notes must contain 1 to 500 characters.",
                    ExitCode.USAGE,
                )

        if self._target_validator is not None:
            self._target_validator(self._target)
        package = self._validator(request.file)
        if not request.dry_run:
            if self._capabilities.atomic_submission:
                ambiguous = self.repository.find_ambiguous(
                    self._target.store.value,
                    self._target.app_id,
                    package.sha256,
                )
                if ambiguous is not None:
                    return OperationResult.failure(
                        store=self._target.store,
                        stage=PublishStage.INTERRUPTED,
                        run_id=ambiguous.run_id,
                        message=(
                            "A previous atomic submission may have reached the store; inspect "
                            "the store console before deleting the local run or publishing again."
                        ),
                        resumable=False,
                    )
            duplicate = self.repository.find_resumable(
                self._target.store.value,
                self._target.app_id,
                package.sha256,
            )
            if duplicate is not None:
                return OperationResult.failure(
                    store=self._target.store,
                    stage=_STAGES[duplicate.state],
                    run_id=duplicate.run_id,
                    message="An unfinished run already exists for this application and package.",
                    resumable=True,
                )

        receipt = self.repository.create(
            store=request.store,
            app_alias=request.app_alias,
            app_id=self._target.app_id,
            package_name=self._target.package_name,
            package_path=str(package.path),
            package_sha256=package.sha256,
            logical_name=package.logical_name,
            release_id=self._target.release_id,
            track=self._target.track,
            release_status=self._target.release_status,
            language=self._target.language,
            release_notes=request.release_notes.strip() if request.release_notes else None,
            submit=request.submit,
        )
        receipt = self._transition(receipt, RunState.VALIDATED)
        if request.dry_run:
            self._transition(receipt, RunState.COMPLETED)
            return OperationResult.success(
                store=self._target.store,
                stage=PublishStage.COMPLETED,
                run_id=receipt.run_id,
                message=(
                    "Configuration and package validation succeeded; no network calls were made."
                ),
            )
        return await self._continue(
            receipt,
            poll_interval=request.poll_interval_seconds,
            wait_timeout=request.wait_timeout_seconds,
        )

    async def resume(
        self,
        run_id: str,
        *,
        poll_interval: float = 15.0,
        wait_timeout: float = 600.0,
    ) -> OperationResult:
        receipt = self.repository.get(run_id)
        if self._capabilities.atomic_submission:
            raise PublishingError(
                "ATOMIC_RUN_NOT_RESUMABLE",
                "Atomic store submissions cannot be resumed safely; inspect the store console.",
                ExitCode.LOCAL_STATE,
            )
        if (
            receipt.store is not self._target.store
            or receipt.app_id != self._target.app_id
            or receipt.package_name != self._target.package_name
            or receipt.release_id != self._target.release_id
            or receipt.track != self._target.track
            or receipt.release_status != self._target.release_status
        ):
            raise PublishingError(
                "RUN_APP_MISMATCH",
                "The selected run does not belong to the configured application.",
                ExitCode.LOCAL_STATE,
            )
        if not receipt.resumable:
            raise PublishingError(
                "RUN_NOT_RESUMABLE",
                "The selected publishing run is not resumable.",
                ExitCode.LOCAL_STATE,
            )
        package = self._validator(Path(receipt.package_path))
        if package.sha256 != receipt.package_sha256:
            raise PublishingError(
                "PACKAGE_CHANGED",
                "The package changed after this publishing run was created.",
                ExitCode.PACKAGE_VALIDATION,
            )
        return await self._continue(
            receipt,
            poll_interval=poll_interval,
            wait_timeout=wait_timeout,
        )

    async def _continue(
        self,
        receipt: RunReceipt,
        *,
        poll_interval: float,
        wait_timeout: float,
    ) -> OperationResult:
        try:
            if receipt.state in {RunState.CREATED, RunState.VALIDATED}:
                await self._adapter.verify(target=self._target)
                receipt = self._transition(receipt, RunState.APP_VERIFIED)

            if receipt.state is RunState.APP_VERIFIED and self._capabilities.atomic_submission:
                package = self._validator(Path(receipt.package_path))
                if package.sha256 != receipt.package_sha256:
                    raise PublishingError(
                        "PACKAGE_CHANGED",
                        "The package changed after this publishing run was created.",
                        ExitCode.PACKAGE_VALIDATION,
                    )
                receipt = self._transition(receipt, RunState.SUBMISSION_STARTED)
                atomic_adapter = cast(AtomicStoreAdapter, self._adapter)
                submission_id = await atomic_adapter.publish_atomic(
                    target=self._target,
                    artifact=package,
                    release_notes=receipt.release_notes,
                )
                receipt = self._transition(
                    receipt,
                    RunState.SUBMITTED,
                    artifact_id=package.sha256,
                    submission_id=submission_id,
                )

            if receipt.state is RunState.APP_VERIFIED:
                package = self._validator(Path(receipt.package_path))
                if package.sha256 != receipt.package_sha256:
                    raise PublishingError(
                        "PACKAGE_CHANGED",
                        "The package changed after this publishing run was created.",
                        ExitCode.PACKAGE_VALIDATION,
                    )
                bound = await self._adapter.upload(target=self._target, artifact=package)
                receipt = self._transition(
                    receipt,
                    RunState.PACKAGE_BOUND,
                    artifact_id=bound.artifact_id,
                    operation_id=bound.operation_id,
                )

            if (
                receipt.state is RunState.PACKAGE_BOUND
                and not self._capabilities.requires_processing_poll
            ):
                receipt = self._transition(receipt, RunState.PACKAGE_READY)

            if self._capabilities.requires_processing_poll and receipt.state in {
                RunState.PACKAGE_BOUND,
                RunState.PACKAGE_COMPILING,
                RunState.TIMED_OUT,
                RunState.INTERRUPTED,
            }:
                if not receipt.artifact_id:
                    raise PublishingError(
                        "RUN_PACKAGE_VERSION_MISSING",
                        "The run cannot resume because its artifact ID is missing.",
                        ExitCode.LOCAL_STATE,
                    )
                receipt = self._transition(receipt, RunState.PACKAGE_COMPILING)
                ready = await self._wait_until_ready(
                    receipt,
                    poll_interval=poll_interval,
                    wait_timeout=wait_timeout,
                )
                if ready is None:
                    receipt = self._transition(receipt, RunState.TIMED_OUT)
                    return OperationResult.failure(
                        store=self._target.store,
                        stage=PublishStage.TIMED_OUT,
                        run_id=receipt.run_id,
                        message=f"{self._target.label} is still processing the artifact.",
                        resumable=True,
                    )
                updates: dict[str, object] = {}
                if ready.artifact_id is not None:
                    updates["artifact_id"] = ready.artifact_id
                receipt = self._transition(receipt, RunState.PACKAGE_READY, **updates)

            if not receipt.submit and receipt.state is RunState.PACKAGE_READY:
                self._transition(receipt, RunState.COMPLETED)
                return OperationResult.success(
                    store=self._target.store,
                    stage=PublishStage.PACKAGE_READY,
                    run_id=receipt.run_id,
                    message=(
                        f"{self._target.label} artifact is ready; review submission was skipped."
                    ),
                )

            if receipt.state is RunState.PACKAGE_READY:
                if self._capabilities.requires_release_notes and not receipt.release_notes:
                    raise PublishingError(
                        "RELEASE_NOTES_MISSING",
                        "The resumable run does not contain release notes.",
                        ExitCode.LOCAL_STATE,
                    )
                if not receipt.artifact_id:
                    raise PublishingError(
                        "RUN_ARTIFACT_ID_MISSING",
                        "The run cannot prepare a release because its artifact ID is missing.",
                        ExitCode.LOCAL_STATE,
                    )
                await self._adapter.prepare_release(
                    target=self._target,
                    artifact_id=receipt.artifact_id,
                    operation_id=receipt.operation_id,
                    release_notes=receipt.release_notes,
                )
                receipt = self._transition(receipt, RunState.METADATA_UPDATED)

            if receipt.state is RunState.METADATA_UPDATED:
                if not receipt.artifact_id:
                    raise PublishingError(
                        "RUN_ARTIFACT_ID_MISSING",
                        "The run cannot submit because its artifact ID is missing.",
                        ExitCode.LOCAL_STATE,
                    )
                try:
                    submission_id = await self._adapter.submit(
                        target=self._target,
                        artifact_id=receipt.artifact_id,
                        operation_id=receipt.operation_id,
                    )
                except ArtifactStillProcessingError as error:
                    receipt = self._transition(receipt, RunState.PACKAGE_COMPILING)
                    ready = await self._wait_until_ready(
                        receipt,
                        poll_interval=poll_interval,
                        wait_timeout=wait_timeout,
                    )
                    if ready is None:
                        receipt = self._transition(receipt, RunState.TIMED_OUT)
                        return OperationResult.failure(
                            store=self._target.store,
                            stage=PublishStage.TIMED_OUT,
                            run_id=receipt.run_id,
                            message=error.message,
                            resumable=True,
                            vendor_code=error.vendor_code,
                        )
                    updates = {}
                    if ready.artifact_id is not None:
                        updates["artifact_id"] = ready.artifact_id
                    receipt = self._transition(receipt, RunState.METADATA_UPDATED, **updates)
                    assert receipt.artifact_id is not None
                    submission_id = await self._adapter.submit(
                        target=self._target,
                        artifact_id=receipt.artifact_id,
                        operation_id=receipt.operation_id,
                    )
                receipt = self._transition(
                    receipt,
                    RunState.SUBMITTED,
                    submission_id=submission_id,
                )

            if receipt.state is RunState.SUBMITTED:
                self._transition(receipt, RunState.COMPLETED)
                return OperationResult.success(
                    store=self._target.store,
                    stage=PublishStage.SUBMITTED,
                    run_id=receipt.run_id,
                    message=f"{self._target.label} accepted the review submission.",
                )
            raise PublishingError(
                "RUN_STATE_UNSUPPORTED",
                f"Cannot continue publishing from state: {receipt.state.value}",
                ExitCode.LOCAL_STATE,
            )
        except asyncio.CancelledError:
            if self._capabilities.atomic_submission:
                uncertain_state = (
                    RunState.SUBMISSION_UNCERTAIN
                    if receipt.state is RunState.SUBMISSION_STARTED
                    else RunState.FAILED
                )
                interrupted = self._transition(receipt, uncertain_state)
                return OperationResult.failure(
                    store=self._target.store,
                    stage=PublishStage.INTERRUPTED,
                    run_id=interrupted.run_id,
                    message=(
                        "Atomic publishing was interrupted; inspect the store console before "
                        "publishing again."
                    ),
                    resumable=False,
                )
            # Preserve the last completed stage. Retrying that stage is how each adapter
            # reconciles remote state after a response is lost (including uploads and submits).
            interrupted = self._transition(receipt, receipt.state)
            return OperationResult.failure(
                store=self._target.store,
                stage=PublishStage.INTERRUPTED,
                run_id=interrupted.run_id,
                message="Publishing was interrupted.",
                resumable=interrupted.resumable,
            )
        except StoreHelperError as error:
            if (
                self._capabilities.atomic_submission
                and receipt.state is RunState.SUBMISSION_STARTED
                and error.exit_code is ExitCode.NETWORK
            ):
                self._transition(receipt, RunState.SUBMISSION_UNCERTAIN)
                raise
            if error.resumable and receipt.artifact_id:
                # Once a processed build is attached or ready for submission, its artifact_id
                # is a build ID rather than an upload ID. Preserve that completed stage so
                # resume retries the idempotent prepare/submit operation instead of repolling.
                recovery_state = (
                    receipt.state
                    if receipt.state in {RunState.PACKAGE_READY, RunState.METADATA_UPDATED}
                    else RunState.TIMED_OUT
                )
                resumable = self._transition(receipt, recovery_state)
                return OperationResult.failure(
                    store=self._target.store,
                    stage=PublishStage.TIMED_OUT,
                    run_id=resumable.run_id,
                    message=error.message,
                    resumable=True,
                    vendor_code=error.vendor_code,
                )
            self._transition(receipt, RunState.FAILED)
            raise

    async def _wait_until_ready(
        self,
        receipt: RunReceipt,
        *,
        poll_interval: float,
        wait_timeout: float,
    ) -> ProcessingStatus | None:
        assert receipt.artifact_id is not None
        started = self._clock()
        while True:
            status = await self._adapter.processing_status(
                target=self._target,
                artifact_id=receipt.artifact_id,
                operation_id=receipt.operation_id,
            )
            if status.state is ProcessingState.READY:
                return status
            if status.state is ProcessingState.FAILED:
                raise StoreVendorError(
                    "ARTIFACT_PROCESSING_FAILED",
                    status.reason or "The store could not process the artifact.",
                    ExitCode.VENDOR_REJECTION,
                )
            if self._clock() - started >= wait_timeout:
                return None
            await self._sleeper(poll_interval)

    async def status(self) -> OperationResult:
        if not self._capabilities.supports_review_status:
            raise PublishingError(
                "STATUS_UNSUPPORTED",
                "The selected store does not support review status queries.",
                ExitCode.USAGE,
            )
        state = await self._adapter.review_status(target=self._target)
        return OperationResult.success(
            store=self._target.store,
            stage=PublishStage.COMPLETED,
            run_id=None,
            message=f"{self._target.label} review status: {state.value}",
        )
