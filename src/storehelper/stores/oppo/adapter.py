"""Store-neutral staged OPPO Software Store adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from storehelper.artifacts.models import ArtifactInfo
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.models import (
    ProcessingStatus,
    ReviewStatus,
    StoreName,
    StoreTarget,
    UploadedArtifact,
    VerifiedApplication,
)
from storehelper.stores.oppo.client import OppoClient
from storehelper.stores.oppo.errors import OppoVendorError
from storehelper.stores.oppo.models import OppoApplicationInfo, OppoUploadedApk
from storehelper.stores.oppo.package import MAX_OPPO_PACKAGE_SIZE, OppoArtifactInfo

_PUBLISHABLE_AUDIT_STATES = frozenset({2, 6, 7, 111})
_AUDIT_STATUS_MAP = {
    0: ReviewStatus.PENDING_REVIEW,
    1: ReviewStatus.IN_REVIEW,
    2: ReviewStatus.APPROVED,
    3: ReviewStatus.REJECTED,
    4: ReviewStatus.IN_REVIEW,
    5: ReviewStatus.REJECTED,
    6: ReviewStatus.APPROVED,
    7: ReviewStatus.APPROVED,
    111: ReviewStatus.APPROVED,
    222: ReviewStatus.SUSPENDED,
    444: ReviewStatus.REJECTED,
}


def map_oppo_review_status(raw: object) -> ReviewStatus:
    if isinstance(raw, bool):
        return ReviewStatus.UNKNOWN
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return ReviewStatus.UNKNOWN
    return _AUDIT_STATUS_MAP.get(value, ReviewStatus.UNKNOWN)


@dataclass(frozen=True, repr=False)
class _StagedSubmission:
    package_name: str
    version_code: int
    release_notes: str
    application: OppoApplicationInfo
    uploaded: OppoUploadedApk

    def __repr__(self) -> str:
        return "_StagedSubmission(redacted=True)"


class OppoAdapter:
    def __init__(self, client: OppoClient) -> None:
        self._client = client
        self._staged: _StagedSubmission | None = None

    @staticmethod
    def _validate_target(target: StoreTarget) -> tuple[str, int]:
        if (
            target.store is not StoreName.OPPO
            or target.app_id != target.package_name
            or not target.package_name.strip()
            or target.version_code is None
            or target.version_code <= 0
        ):
            raise OppoVendorError(
                "OPPO_TARGET_INVALID",
                "OPPO publishing requires one matching existing package and a version code.",
                ExitCode.VENDOR_REJECTION,
            )
        return target.package_name, target.version_code

    @staticmethod
    def _validate_release_notes(release_notes: str | None) -> str:
        notes = release_notes.strip() if release_notes is not None else ""
        if not 1 <= len(notes) <= 500:
            raise OppoVendorError(
                "OPPO_RELEASE_NOTES_INVALID",
                "OPPO release notes must contain 1 to 500 characters.",
                ExitCode.VENDOR_REJECTION,
            )
        return notes

    @staticmethod
    def _validate_listing_bounds(application: OppoApplicationInfo) -> None:
        if (
            len(application.app_name) > 50
            or len(application.summary) > 15
            or len(application.detail_desc) > 4000
            or any(
                len(value) > 8000
                for value in (
                    application.privacy_source_url,
                    application.icon_url,
                    application.pic_url,
                    application.copyright_url,
                )
            )
        ):
            raise OppoVendorError(
                "OPPO_APPLICATION_INCOMPLETE",
                "The existing OPPO application information exceeds supported vendor limits.",
                ExitCode.VENDOR_REJECTION,
            )

    async def _application_for_publish(self, target: StoreTarget) -> OppoApplicationInfo:
        package_name, version_code = self._validate_target(target)
        application = await self._client.application_info(package_name)
        if version_code <= application.version_code:
            raise OppoVendorError(
                "OPPO_VERSION_CONFLICT",
                "The configured OPPO version code must exceed the current version.",
                ExitCode.VENDOR_REJECTION,
            )
        if application.audit_status not in _PUBLISHABLE_AUDIT_STATES:
            raise OppoVendorError(
                "OPPO_REVIEW_CONFLICT",
                "The existing OPPO application is not ready for another version update.",
                ExitCode.VENDOR_REJECTION,
            )
        self._validate_listing_bounds(application)
        return application

    async def verify(self, *, target: StoreTarget) -> VerifiedApplication:
        application = await self._application_for_publish(target)
        return VerifiedApplication(
            app_id=application.package_name,
            package_name=application.package_name,
        )

    async def stage_submission(
        self,
        *,
        target: StoreTarget,
        artifact: ArtifactInfo,
        release_notes: str | None,
    ) -> None:
        self._staged = None
        package_name, version_code = self._validate_target(target)
        notes = self._validate_release_notes(release_notes)
        if (
            not isinstance(artifact, OppoArtifactInfo)
            or artifact.kind != "apk"
            or artifact.path.suffix.lower() != ".apk"
            or artifact.size > MAX_OPPO_PACKAGE_SIZE
        ):
            raise OppoVendorError(
                "OPPO_PACKAGE_INVALID",
                "OPPO publishing requires one validated APK no larger than 2 GiB.",
                ExitCode.PACKAGE_VALIDATION,
            )
        application = await self._application_for_publish(target)
        uploaded = await self._client.upload_apk(artifact)
        self._staged = _StagedSubmission(
            package_name=package_name,
            version_code=version_code,
            release_notes=notes,
            application=application,
            uploaded=uploaded,
        )

    async def commit_staged_submission(self, *, target: StoreTarget) -> str:
        package_name, version_code = self._validate_target(target)
        staged = self._staged
        if staged is None:
            raise OppoVendorError(
                "OPPO_STAGED_CONTEXT_MISSING",
                "The in-memory OPPO upload context is not available; start a new publish.",
                ExitCode.LOCAL_STATE,
            )
        if staged.package_name != package_name or staged.version_code != version_code:
            self._staged = None
            raise OppoVendorError(
                "OPPO_STAGED_CONTEXT_MISMATCH",
                "The OPPO upload context does not match this publish target.",
                ExitCode.LOCAL_STATE,
            )
        self._staged = None
        return await self._client.submit_update(
            application=staged.application,
            uploaded=staged.uploaded,
            version_code=staged.version_code,
            release_notes=staged.release_notes,
        )

    async def review_status(self, *, target: StoreTarget) -> ReviewStatus:
        package_name, _ = self._validate_target(target)
        application = await self._client.application_info(package_name)
        return map_oppo_review_status(application.audit_status)

    @staticmethod
    def _staged_only() -> NoReturn:
        raise OppoVendorError(
            "OPPO_STAGED_ONLY",
            "OPPO publishing uses one in-memory staged submission.",
            ExitCode.LOCAL_STATE,
        )

    async def upload(
        self,
        *,
        target: StoreTarget,
        artifact: ArtifactInfo,
    ) -> UploadedArtifact:
        self._staged_only()

    async def processing_status(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> ProcessingStatus:
        self._staged_only()

    async def prepare_release(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
        release_notes: str | None,
    ) -> None:
        self._staged_only()

    async def submit(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> str:
        self._staged_only()
