"""Resumable publishing orchestration independent of CLI presentation."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from storehelper.config.models import ApplicationConfig, HuaweiStoreConfig
from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode
from storehelper.domain.models import OperationResult, PublishRequest, PublishStage
from storehelper.runs.models import RunReceipt, RunState
from storehelper.runs.repository import RunRepository
from storehelper.stores.base import StoreAdapter
from storehelper.stores.huawei.adapter import CompileState
from storehelper.stores.huawei.errors import HuaweiVendorError
from storehelper.stores.huawei.package import validate_package

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]

_STAGES = {
    RunState.CREATED: PublishStage.CREATED,
    RunState.VALIDATED: PublishStage.VALIDATED,
    RunState.APP_VERIFIED: PublishStage.APP_VERIFIED,
    RunState.PACKAGE_BOUND: PublishStage.PACKAGE_BOUND,
    RunState.PACKAGE_COMPILING: PublishStage.PACKAGE_COMPILING,
    RunState.PACKAGE_READY: PublishStage.PACKAGE_READY,
    RunState.METADATA_UPDATED: PublishStage.METADATA_UPDATED,
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
        application: ApplicationConfig,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._adapter = adapter
        self.repository = repository
        self._application = application
        self._clock = clock
        self._sleeper = sleeper

    @property
    def _huawei(self) -> HuaweiStoreConfig:
        return self._application.stores.huawei

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
        if request.submit and not request.dry_run:
            if not request.confirmed:
                raise PublishingError(
                    "PUBLISH_CONFIRMATION_REQUIRED",
                    "Full publishing requires explicit confirmation or --yes.",
                    ExitCode.USAGE,
                )
            notes = (request.release_notes or "").strip()
            if not 1 <= len(notes) <= 500:
                raise PublishingError(
                    "RELEASE_NOTES_INVALID",
                    "Release notes must contain 1 to 500 characters.",
                    ExitCode.USAGE,
                )

        package = validate_package(request.file)
        if not request.dry_run:
            duplicate = self.repository.find_resumable(
                "huawei",
                self._huawei.app_id,
                package.sha256,
            )
            if duplicate is not None:
                return OperationResult.failure(
                    stage=_STAGES[duplicate.state],
                    run_id=duplicate.run_id,
                    message="An unfinished run already exists for this application and package.",
                    resumable=True,
                )

        receipt = self.repository.create(
            app_alias=request.app_alias,
            app_id=self._huawei.app_id,
            package_name=self._application.package_name,
            package_path=str(package.path),
            package_sha256=package.sha256,
            logical_name=package.logical_name,
            language=self._huawei.language,
            release_notes=request.release_notes.strip() if request.release_notes else None,
            submit=request.submit,
        )
        receipt = self._transition(receipt, RunState.VALIDATED)
        if request.dry_run:
            self._transition(receipt, RunState.COMPLETED)
            return OperationResult.success(
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
        if (
            receipt.app_id != self._huawei.app_id
            or receipt.package_name != self._application.package_name
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
                await self._adapter.verify(
                    app_id=receipt.app_id,
                    package_name=receipt.package_name,
                )
                receipt = self._transition(receipt, RunState.APP_VERIFIED)

            if receipt.state is RunState.APP_VERIFIED:
                package = validate_package(Path(receipt.package_path))
                if package.sha256 != receipt.package_sha256:
                    raise PublishingError(
                        "PACKAGE_CHANGED",
                        "The package changed after this publishing run was created.",
                        ExitCode.PACKAGE_VALIDATION,
                    )
                bound = await self._adapter.upload(app_id=receipt.app_id, package=package)
                receipt = self._transition(
                    receipt,
                    RunState.PACKAGE_BOUND,
                    pkg_version=bound.pkg_version,
                )

            if receipt.state in {
                RunState.PACKAGE_BOUND,
                RunState.PACKAGE_COMPILING,
                RunState.TIMED_OUT,
                RunState.INTERRUPTED,
            }:
                if not receipt.pkg_version:
                    raise PublishingError(
                        "RUN_PACKAGE_VERSION_MISSING",
                        "The run cannot resume because pkgVersion is missing.",
                        ExitCode.LOCAL_STATE,
                    )
                receipt = self._transition(receipt, RunState.PACKAGE_COMPILING)
                ready = await self._wait_until_ready(
                    receipt,
                    poll_interval=poll_interval,
                    wait_timeout=wait_timeout,
                )
                if not ready:
                    receipt = self._transition(receipt, RunState.TIMED_OUT)
                    return OperationResult.failure(
                        stage=PublishStage.TIMED_OUT,
                        run_id=receipt.run_id,
                        message="Huawei is still compiling the package.",
                        resumable=True,
                    )
                receipt = self._transition(receipt, RunState.PACKAGE_READY)

            if not receipt.submit and receipt.state is RunState.PACKAGE_READY:
                self._transition(receipt, RunState.COMPLETED)
                return OperationResult.success(
                    stage=PublishStage.PACKAGE_READY,
                    run_id=receipt.run_id,
                    message="Huawei package is ready; review submission was skipped.",
                )

            if receipt.state is RunState.PACKAGE_READY:
                if not receipt.release_notes:
                    raise PublishingError(
                        "RELEASE_NOTES_MISSING",
                        "The resumable run does not contain release notes.",
                        ExitCode.LOCAL_STATE,
                    )
                await self._adapter.update_release_notes(
                    app_id=receipt.app_id,
                    language=receipt.language,
                    release_notes=receipt.release_notes,
                )
                receipt = self._transition(receipt, RunState.METADATA_UPDATED)

            if receipt.state is RunState.METADATA_UPDATED:
                try:
                    await self._adapter.submit(app_id=receipt.app_id)
                except HuaweiVendorError as error:
                    if error.code != "HUAWEI_PACKAGE_COMPILING":
                        raise
                    receipt = self._transition(receipt, RunState.PACKAGE_COMPILING)
                    ready = await self._wait_until_ready(
                        receipt,
                        poll_interval=poll_interval,
                        wait_timeout=wait_timeout,
                    )
                    if not ready:
                        receipt = self._transition(receipt, RunState.TIMED_OUT)
                        return OperationResult.failure(
                            stage=PublishStage.TIMED_OUT,
                            run_id=receipt.run_id,
                            message=error.message,
                            resumable=True,
                            vendor_code=error.vendor_code,
                        )
                    receipt = self._transition(receipt, RunState.METADATA_UPDATED)
                    await self._adapter.submit(app_id=receipt.app_id)
                receipt = self._transition(receipt, RunState.SUBMITTED)

            if receipt.state is RunState.SUBMITTED:
                self._transition(receipt, RunState.COMPLETED)
                return OperationResult.success(
                    stage=PublishStage.SUBMITTED,
                    run_id=receipt.run_id,
                )
            raise PublishingError(
                "RUN_STATE_UNSUPPORTED",
                f"Cannot continue publishing from state: {receipt.state.value}",
                ExitCode.LOCAL_STATE,
            )
        except asyncio.CancelledError:
            interrupted = self._transition(receipt, RunState.INTERRUPTED)
            return OperationResult.failure(
                stage=PublishStage.INTERRUPTED,
                run_id=interrupted.run_id,
                message="Publishing was interrupted.",
                resumable=interrupted.pkg_version is not None,
            )
        except StoreHelperError as error:
            if error.resumable and receipt.pkg_version:
                resumable = self._transition(receipt, RunState.TIMED_OUT)
                return OperationResult.failure(
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
    ) -> bool:
        assert receipt.pkg_version is not None
        started = self._clock()
        while True:
            status = await self._adapter.compile_status(
                app_id=receipt.app_id,
                pkg_version=receipt.pkg_version,
            )
            if status.state is CompileState.READY:
                return True
            if status.state is CompileState.FAILED:
                raise HuaweiVendorError(
                    "HUAWEI_COMPILE_FAILED",
                    status.reason or "Huawei package compilation failed.",
                    ExitCode.VENDOR_REJECTION,
                )
            if self._clock() - started >= wait_timeout:
                return False
            await self._sleeper(poll_interval)

    async def status(self) -> OperationResult:
        state = await self._adapter.review_status(app_id=self._huawei.app_id)
        return OperationResult.success(
            stage=PublishStage.COMPLETED,
            run_id=None,
            message=f"Huawei review status: {state.value}",
        )
