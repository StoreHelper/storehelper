"""Store-neutral staged vivo App Store adapter."""

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
from storehelper.stores.vivo.client import VivoClient
from storehelper.stores.vivo.errors import VivoVendorError
from storehelper.stores.vivo.models import VivoApplicationInfo, VivoUploadedApk
from storehelper.stores.vivo.package import (
    MAX_VIVO_PACKAGE_SIZE,
    VivoArtifactInfo,
)

_PUBLISHABLE_STATES = frozenset({3, 4, 5})
_STATUS_MAP = {
    1: ReviewStatus.PENDING_REVIEW,
    2: ReviewStatus.IN_REVIEW,
    3: ReviewStatus.APPROVED,
    4: ReviewStatus.REJECTED,
    5: ReviewStatus.APPROVED,
    6: ReviewStatus.SUSPENDED,
}


def map_vivo_review_status(raw: object) -> ReviewStatus:
    if isinstance(raw, bool):
        return ReviewStatus.UNKNOWN
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return ReviewStatus.UNKNOWN
    return _STATUS_MAP.get(value, ReviewStatus.UNKNOWN)


@dataclass(frozen=True, repr=False)
class _StagedSubmission:
    package_name: str
    version_code: int
    release_notes: str
    application: VivoApplicationInfo
    uploaded: VivoUploadedApk

    def __repr__(self) -> str:
        return "_StagedSubmission(redacted=True)"


class VivoAdapter:
    def __init__(self, client: VivoClient) -> None:
        self._client = client
        self._staged: _StagedSubmission | None = None

    @staticmethod
    def _validate_target(target: StoreTarget) -> tuple[str, int]:
        if (
            target.store is not StoreName.VIVO
            or target.app_id != target.package_name
            or not target.package_name.strip()
            or target.version_code is None
            or target.version_code <= 0
        ):
            raise VivoVendorError(
                "VIVO_TARGET_INVALID",
                "vivo publishing requires one matching existing package and a version code.",
                ExitCode.VENDOR_REJECTION,
            )
        return target.package_name, target.version_code

    @staticmethod
    def _validate_release_notes(release_notes: str | None) -> str:
        notes = release_notes.strip() if release_notes is not None else ""
        if not 5 <= len(notes) <= 200:
            raise VivoVendorError(
                "VIVO_RELEASE_NOTES_INVALID",
                "vivo release notes must contain 5 to 200 characters.",
                ExitCode.VENDOR_REJECTION,
            )
        return notes

    async def _application_for_publish(self, target: StoreTarget) -> VivoApplicationInfo:
        package_name, version_code = self._validate_target(target)
        application = await self._client.application_info(package_name)
        if version_code <= application.version_code:
            raise VivoVendorError(
                "VIVO_VERSION_CONFLICT",
                "The configured vivo version code must be greater than the current version.",
                ExitCode.VENDOR_REJECTION,
            )
        if application.status not in _PUBLISHABLE_STATES:
            raise VivoVendorError(
                "VIVO_REVIEW_CONFLICT",
                "The existing vivo application is not ready for another version update.",
                ExitCode.VENDOR_REJECTION,
            )
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
            not isinstance(artifact, VivoArtifactInfo)
            or artifact.kind != "apk"
            or artifact.path.suffix.lower() != ".apk"
            or artifact.size > MAX_VIVO_PACKAGE_SIZE
        ):
            raise VivoVendorError(
                "VIVO_PACKAGE_INVALID",
                "vivo publishing requires one validated APK no larger than 3 GiB.",
                ExitCode.PACKAGE_VALIDATION,
            )
        application = await self._application_for_publish(target)
        uploaded = await self._client.upload_apk(
            package_name=package_name,
            artifact=artifact,
        )
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
            raise VivoVendorError(
                "VIVO_STAGED_CONTEXT_MISSING",
                "The in-memory vivo upload context is not available; start a new publish.",
                ExitCode.LOCAL_STATE,
            )
        if staged.package_name != package_name or staged.version_code != version_code:
            self._staged = None
            raise VivoVendorError(
                "VIVO_STAGED_CONTEXT_MISMATCH",
                "The vivo upload context does not match this publish target.",
                ExitCode.LOCAL_STATE,
            )
        self._staged = None
        return await self._client.submit_update(
            package_name=staged.package_name,
            version_code=staged.version_code,
            uploaded=staged.uploaded,
            release_notes=staged.release_notes,
        )

    async def review_status(self, *, target: StoreTarget) -> ReviewStatus:
        package_name, _ = self._validate_target(target)
        application = await self._client.application_info(package_name)
        return map_vivo_review_status(application.status)

    @staticmethod
    def _staged_only() -> NoReturn:
        raise VivoVendorError(
            "VIVO_STAGED_ONLY",
            "vivo publishing uses one in-memory staged submission.",
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
