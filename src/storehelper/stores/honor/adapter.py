"""Conservative existing-application HONOR publishing adapter."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable

from storehelper.artifacts.models import ArtifactInfo
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.honor.client import HonorClient
from storehelper.stores.honor.errors import HonorVendorError
from storehelper.stores.honor.models import (
    HonorApplicationInfo,
    HonorCurrentRelease,
    HonorLocaleInfo,
)
from storehelper.stores.huawei.package import PackageInfo, PackageKind
from storehelper.stores.models import (
    ProcessingState,
    ProcessingStatus,
    ReviewStatus,
    StoreName,
    StoreTarget,
    UploadedArtifact,
    VerifiedApplication,
)

_AUDIT_STATUS = {
    0: ReviewStatus.IN_REVIEW,
    1: ReviewStatus.APPROVED,
    2: ReviewStatus.REJECTED,
    3: ReviewStatus.UNKNOWN,
    4: ReviewStatus.PENDING_REVIEW,
}
_RECEIPT_PATTERN = re.compile(r"^([1-9][0-9]*):([0-9a-f]{64})$")
Sleeper = Callable[[float], Awaitable[None]]


def parse_review_status(value: object) -> ReviewStatus:
    if not isinstance(value, int) or isinstance(value, bool):
        return ReviewStatus.UNKNOWN
    return _AUDIT_STATUS.get(value, ReviewStatus.UNKNOWN)


class HonorAdapter:
    def __init__(
        self,
        client: HonorClient,
        *,
        reconcile_sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._client = client
        self._reconcile_sleeper = reconcile_sleeper

    def __repr__(self) -> str:
        return "HonorAdapter(configured=True)"

    @property
    def client(self) -> HonorClient:
        return self._client

    @staticmethod
    def _validate_target(target: StoreTarget) -> int:
        if (
            target.store is not StoreName.HONOR
            or target.app_id != target.package_name
            or target.version_code is None
            or target.version_code <= 0
        ):
            raise HonorVendorError(
                "HONOR_TARGET_INVALID",
                "HONOR target requires an exact package and positive version code.",
                ExitCode.VENDOR_REJECTION,
            )
        return target.version_code

    @staticmethod
    def _selected_locale(detail: HonorApplicationInfo, language: str) -> HonorLocaleInfo:
        matches = [locale for locale in detail.locales if locale.language_id == language]
        if len(matches) != 1:
            raise HonorVendorError(
                "HONOR_LOCALE_NOT_FOUND",
                "The configured HONOR locale must match exactly one existing localization.",
                ExitCode.VENDOR_REJECTION,
            )
        return matches[0]

    @staticmethod
    def _receipt_context(*, artifact_id: str, operation_id: str | None) -> tuple[int, int, str]:
        match = _RECEIPT_PATTERN.fullmatch(artifact_id)
        if (
            match is None
            or operation_id is None
            or not operation_id.isascii()
            or not operation_id.isdigit()
            or operation_id.startswith("0")
        ):
            raise HonorVendorError(
                "HONOR_RECEIPT_INVALID",
                "The HONOR recovery receipt is invalid.",
                ExitCode.LOCAL_STATE,
            )
        return int(operation_id), int(match.group(1)), match.group(2)

    async def _validate_receipt_application(self, *, target: StoreTarget, app_id: int) -> None:
        resolved_app_id = await self._client.get_app_id(package_name=target.package_name)
        if resolved_app_id != app_id:
            raise HonorVendorError(
                "HONOR_RECEIPT_MISMATCH",
                "The HONOR recovery receipt belongs to another application.",
                ExitCode.LOCAL_STATE,
            )

    @staticmethod
    def _release_id_if_reconciled(
        current: HonorCurrentRelease | None, *, target_version: int
    ) -> str | None:
        if (
            current is not None
            and current.version_code == target_version
            and current.audit_result in {0, 1, 2}
            and current.release_id is not None
        ):
            return current.release_id
        return None

    @staticmethod
    def _validate_release_state(
        current: HonorCurrentRelease | None,
        *,
        published_version: int,
        target_version: int,
    ) -> None:
        if current is None:
            raise HonorVendorError(
                "HONOR_REVIEW_STATE_UNKNOWN",
                "HONOR did not return a current release state.",
                ExitCode.VENDOR_REJECTION,
            )
        if current.audit_result == 0:
            raise HonorVendorError(
                "HONOR_REVIEW_CONFLICT",
                "The HONOR application already has a release in review.",
                ExitCode.VENDOR_REJECTION,
            )
        if current.audit_result == 4:
            raise HonorVendorError(
                "HONOR_DRAFT_CONFLICT",
                "The HONOR application already has an editing draft.",
                ExitCode.VENDOR_REJECTION,
            )
        if current.audit_result not in {1, 2}:
            raise HonorVendorError(
                "HONOR_REVIEW_STATE_UNKNOWN",
                "The HONOR application is in an unsupported release state.",
                ExitCode.VENDOR_REJECTION,
            )
        if current.audit_result == 1 and (
            current.version_code is not None and current.version_code > published_version
        ):
            raise HonorVendorError(
                "HONOR_REVIEW_CONFLICT",
                "HONOR has an approved release that is not the current published version.",
                ExitCode.VENDOR_REJECTION,
            )
        if current.version_code is not None and current.version_code > target_version:
            raise HonorVendorError(
                "HONOR_VERSION_CONFLICT",
                "The configured HONOR version is older than the current release request.",
                ExitCode.VENDOR_REJECTION,
            )

    async def _application(self, target: StoreTarget) -> tuple[int, HonorApplicationInfo]:
        target_version = self._validate_target(target)
        app_id = await self._client.get_app_id(package_name=target.package_name)
        detail = await self._client.get_app_detail(app_id=app_id)
        if detail.package_name != target.package_name:
            raise HonorVendorError(
                "HONOR_PACKAGE_MISMATCH",
                "HONOR returned a different package for the resolved APPID.",
                ExitCode.VENDOR_REJECTION,
            )
        if target_version <= detail.published_version_code:
            raise HonorVendorError(
                "HONOR_VERSION_CONFLICT",
                "The configured HONOR version must be greater than the published version.",
                ExitCode.VENDOR_REJECTION,
            )
        self._selected_locale(detail, target.language)
        current = await self._client.get_current_release(app_id=app_id)
        self._validate_release_state(
            current,
            published_version=detail.published_version_code,
            target_version=target_version,
        )
        return app_id, detail

    async def verify(self, *, target: StoreTarget) -> VerifiedApplication:
        app_id, _ = await self._application(target)
        return VerifiedApplication(app_id=str(app_id), package_name=target.package_name)

    async def processing_status(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> ProcessingStatus:
        self._validate_target(target)
        return ProcessingStatus(state=ProcessingState.READY, artifact_id=artifact_id)

    async def upload(self, *, target: StoreTarget, artifact: ArtifactInfo) -> UploadedArtifact:
        if not isinstance(artifact, PackageInfo) or artifact.kind is not PackageKind.APK:
            raise HonorVendorError(
                "HONOR_ARTIFACT_INVALID",
                "HONOR publishing requires a validated APK artifact.",
                ExitCode.PACKAGE_VALIDATION,
            )
        app_id, _ = await self._application(target)
        allocation = await self._client.allocate_upload(app_id=app_id, artifact=artifact)
        await self._client.upload_file(
            app_id=app_id,
            object_id=allocation.object_id,
            artifact=artifact,
        )
        return UploadedArtifact(
            artifact_id=f"{allocation.object_id}:{artifact.sha256}",
            operation_id=str(app_id),
        )

    async def prepare_release(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
        release_notes: str | None,
    ) -> None:
        target_version = self._validate_target(target)
        app_id, object_id, expected_sha256 = self._receipt_context(
            artifact_id=artifact_id, operation_id=operation_id
        )
        if release_notes is None or not release_notes.strip():
            raise HonorVendorError(
                "HONOR_RELEASE_NOTES_REQUIRED",
                "HONOR publishing requires localized version notes.",
                ExitCode.VENDOR_REJECTION,
            )
        if len(release_notes) > 500:
            raise HonorVendorError(
                "HONOR_RELEASE_NOTES_INVALID",
                "HONOR version notes must not exceed 500 characters.",
                ExitCode.VENDOR_REJECTION,
            )
        await self._validate_receipt_application(target=target, app_id=app_id)
        detail = await self._client.get_app_detail(app_id=app_id)
        if detail.package_name != target.package_name:
            raise HonorVendorError(
                "HONOR_RECEIPT_MISMATCH",
                "The HONOR recovery receipt belongs to another application.",
                ExitCode.LOCAL_STATE,
            )
        if target_version <= detail.published_version_code:
            raise HonorVendorError(
                "HONOR_VERSION_CONFLICT",
                "The configured HONOR version must be greater than the published version.",
                ExitCode.VENDOR_REJECTION,
            )
        locale = self._selected_locale(detail, target.language)
        bound = any(
            file.file_type == 100 and file.sha256 == expected_sha256 for file in detail.files
        )
        if not bound:
            await self._client.bind_file(app_id=app_id, object_id=object_id)
            detail = await self._client.get_app_detail(app_id=app_id)
            bound = any(
                file.file_type == 100 and file.sha256 == expected_sha256 for file in detail.files
            )
            if not bound:
                raise HonorVendorError(
                    "HONOR_FILE_BIND_UNCONFIRMED",
                    "HONOR did not confirm the uploaded APK binding.",
                    ExitCode.VENDOR_REJECTION,
                    resumable=True,
                )
            locale = self._selected_locale(detail, target.language)
        if locale.new_feature != release_notes:
            await self._client.update_language(
                app_id=app_id,
                locale=locale,
                new_feature=release_notes,
            )
            detail = await self._client.get_app_detail(app_id=app_id)
            locale = self._selected_locale(detail, target.language)
            if locale.new_feature != release_notes:
                raise HonorVendorError(
                    "HONOR_LANGUAGE_UPDATE_UNCONFIRMED",
                    "HONOR did not confirm the localized version notes.",
                    ExitCode.VENDOR_REJECTION,
                    resumable=True,
                )

    async def submit(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> str:
        target_version = self._validate_target(target)
        app_id, _, _ = self._receipt_context(artifact_id=artifact_id, operation_id=operation_id)
        await self._validate_receipt_application(target=target, app_id=app_id)
        current = await self._client.get_current_release(app_id=app_id)
        reconciled = self._release_id_if_reconciled(current, target_version=target_version)
        if reconciled is not None:
            return reconciled
        if current is None or current.audit_result == 3:
            raise HonorVendorError(
                "HONOR_SUBMISSION_STATE_UNKNOWN",
                "HONOR did not return a safe submission state.",
                ExitCode.VENDOR_REJECTION,
            )
        if current.audit_result != 4 or current.version_code != target_version:
            raise HonorVendorError(
                "HONOR_SUBMISSION_CONFLICT",
                "The current HONOR release does not match the prepared update.",
                ExitCode.VENDOR_REJECTION,
            )
        try:
            return await self._client.submit_audit(app_id=app_id)
        except HonorVendorError as error:
            if error.code not in {
                "HONOR_NETWORK_ERROR",
                "HONOR_REDIRECT",
                "HONOR_RESPONSE_INVALID",
                "HONOR_SERVICE_UNAVAILABLE",
            }:
                raise
            for attempt in range(1, 4):
                await self._reconcile_sleeper(float(attempt))
                try:
                    current = await self._client.get_current_release(app_id=app_id)
                except HonorVendorError:
                    continue
                reconciled = self._release_id_if_reconciled(current, target_version=target_version)
                if reconciled is not None:
                    return reconciled
            raise error

    async def review_status(self, *, target: StoreTarget) -> ReviewStatus:
        self._validate_target(target)
        app_id = await self._client.get_app_id(package_name=target.package_name)
        current = await self._client.get_current_release(app_id=app_id)
        return parse_review_status(None if current is None else current.audit_result)
