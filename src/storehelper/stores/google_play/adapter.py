"""Typed Google Play publishing adapter."""

from __future__ import annotations

from collections.abc import Mapping

from storehelper.artifacts.models import ArtifactInfo
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.google_play.client import GooglePlayClient
from storehelper.stores.google_play.errors import GoogleVendorError
from storehelper.stores.google_play.package import PackageInfo
from storehelper.stores.models import (
    ProcessingState,
    ProcessingStatus,
    ReviewStatus,
    StoreName,
    StoreTarget,
    UploadedArtifact,
    VerifiedApplication,
)

_LIFECYCLE_STATUS = {
    "RELEASE_LIFECYCLE_STATE_DRAFT": ReviewStatus.PENDING_REVIEW,
    "RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW": ReviewStatus.PENDING_REVIEW,
    "RELEASE_LIFECYCLE_STATE_IN_REVIEW": ReviewStatus.IN_REVIEW,
    "RELEASE_LIFECYCLE_STATE_APPROVED_NOT_PUBLISHED": ReviewStatus.APPROVED,
    "RELEASE_LIFECYCLE_STATE_NOT_APPROVED": ReviewStatus.REJECTED,
    "RELEASE_LIFECYCLE_STATE_PUBLISHED": ReviewStatus.APPROVED,
    "RELEASE_LIFECYCLE_STATE_UNSPECIFIED": ReviewStatus.UNKNOWN,
}
_STATUS_PRIORITY = {
    ReviewStatus.UNKNOWN: 0,
    ReviewStatus.APPROVED: 1,
    ReviewStatus.PENDING_REVIEW: 2,
    ReviewStatus.IN_REVIEW: 3,
    ReviewStatus.REJECTED: 4,
}


def _protocol_error(message: str) -> GoogleVendorError:
    return GoogleVendorError(
        "GOOGLE_RESPONSE_INVALID",
        message,
        ExitCode.VENDOR_REJECTION,
    )


def parse_review_status(payload: Mapping[str, object]) -> ReviewStatus:
    if "releases" not in payload:
        return ReviewStatus.UNKNOWN
    raw_releases = payload.get("releases")
    if not isinstance(raw_releases, list):
        raise _protocol_error("Google Play returned an invalid release list.")
    statuses: list[ReviewStatus] = []
    for release in raw_releases:
        if not isinstance(release, Mapping):
            raise _protocol_error("Google Play returned an invalid release summary.")
        lifecycle = release.get("releaseLifecycleState")
        if not isinstance(lifecycle, str):
            raise _protocol_error("Google Play release lifecycle state is missing.")
        statuses.append(_LIFECYCLE_STATUS.get(lifecycle, ReviewStatus.UNKNOWN))
    if not statuses:
        return ReviewStatus.UNKNOWN
    return max(statuses, key=_STATUS_PRIORITY.__getitem__)


class GooglePlayAdapter:
    def __init__(self, client: GooglePlayClient) -> None:
        self._client = client

    @staticmethod
    def _validate_target(target: StoreTarget) -> tuple[str, str]:
        if (
            target.store is not StoreName.GOOGLE_PLAY
            or target.app_id != target.package_name
            or target.track is None
            or target.release_status not in {"draft", "completed"}
        ):
            raise GoogleVendorError(
                "GOOGLE_TARGET_INVALID",
                "Google Play target requires a package, track, and draft or completed status.",
                ExitCode.VENDOR_REJECTION,
            )
        return target.package_name, target.track

    async def verify(self, *, target: StoreTarget) -> VerifiedApplication:
        package_name, track = self._validate_target(target)
        await self._client.list_releases(package_name=package_name, track=track)
        return VerifiedApplication(app_id=package_name, package_name=package_name)

    async def processing_status(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> ProcessingStatus:
        self._validate_target(target)
        return ProcessingStatus(state=ProcessingState.READY, artifact_id=artifact_id)

    async def upload(
        self,
        *,
        target: StoreTarget,
        artifact: ArtifactInfo,
    ) -> UploadedArtifact:
        package_name, _ = self._validate_target(target)
        if not isinstance(artifact, PackageInfo):
            raise GoogleVendorError(
                "GOOGLE_PACKAGE_INVALID",
                "Google Play publishing requires a validated AAB or APK artifact.",
                ExitCode.PACKAGE_VALIDATION,
            )
        edit_id = await self._client.create_edit(package_name=package_name)
        version_code = await self._client.upload_artifact(
            package_name=package_name,
            edit_id=edit_id,
            artifact=artifact,
        )
        return UploadedArtifact(
            artifact_id=str(version_code),
            operation_id=edit_id,
        )

    @staticmethod
    def _new_release(
        *,
        artifact_id: str,
        release_status: str,
        language: str,
        release_notes: str | None,
    ) -> dict[str, object]:
        release: dict[str, object] = {
            "versionCodes": [artifact_id],
            "status": release_status,
        }
        if release_notes is not None:
            release["releaseNotes"] = [{"language": language, "text": release_notes}]
        return release

    @staticmethod
    def _release_matches(
        release: Mapping[str, object],
        expected: Mapping[str, object],
    ) -> bool:
        if not all(
            release.get(field) == expected.get(field) for field in ("versionCodes", "status")
        ):
            return False
        expected_notes = expected.get("releaseNotes")
        release_notes = release.get("releaseNotes")
        if expected_notes is None:
            return release_notes in (None, [])
        return release_notes == expected_notes

    async def prepare_release(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
        release_notes: str | None,
    ) -> None:
        package_name, track = self._validate_target(target)
        if operation_id is None or not operation_id.strip():
            raise GoogleVendorError(
                "GOOGLE_EDIT_ID_MISSING",
                "Google Play release preparation requires the persisted App Edit ID.",
                ExitCode.LOCAL_STATE,
            )
        try:
            version_code = int(artifact_id)
        except ValueError:
            version_code = 0
        if version_code <= 0:
            raise GoogleVendorError(
                "GOOGLE_VERSION_CODE_INVALID",
                "Google Play release preparation requires a positive version code.",
                ExitCode.LOCAL_STATE,
            )
        normalized_artifact_id = str(version_code)
        notes = release_notes.strip() if release_notes is not None else None
        if notes is not None and not 1 <= len(notes) <= 500:
            raise GoogleVendorError(
                "GOOGLE_RELEASE_NOTES_INVALID",
                "Google Play release notes must contain 1 to 500 characters.",
                ExitCode.VENDOR_REJECTION,
            )
        assert target.release_status is not None
        expected = self._new_release(
            artifact_id=normalized_artifact_id,
            release_status=target.release_status,
            language=target.language,
            release_notes=notes,
        )

        edit_id = operation_id.strip()
        await self._client.get_edit(package_name=package_name, edit_id=edit_id)
        existing = await self._client.get_track(
            package_name=package_name,
            edit_id=edit_id,
            track=track,
        )
        if any(release.get("status") in {"inProgress", "halted"} for release in existing):
            raise GoogleVendorError(
                "GOOGLE_ACTIVE_ROLLOUT",
                "Google Play track has an active staged rollout; StoreHelper will not "
                "overwrite it.",
                ExitCode.VENDOR_REJECTION,
            )
        for release in existing:
            version_codes = release.get("versionCodes")
            if isinstance(version_codes, list) and normalized_artifact_id in version_codes:
                if self._release_matches(release, expected):
                    return
                raise GoogleVendorError(
                    "GOOGLE_VERSION_CONFLICT",
                    "The uploaded version already exists in the track with different settings.",
                    ExitCode.VENDOR_REJECTION,
                )

        releases = [*existing, expected] if target.release_status == "draft" else [expected]
        await self._client.update_track(
            package_name=package_name,
            edit_id=edit_id,
            track=track,
            releases=releases,
            expected_version_code=normalized_artifact_id,
        )

    @staticmethod
    def _expired_edit_error() -> GoogleVendorError:
        return GoogleVendorError(
            "GOOGLE_EDIT_EXPIRED",
            "The Google Play App Edit expired before it could be committed; start a new publish.",
            ExitCode.LOCAL_STATE,
            resumable=False,
        )

    async def _reconcile_missing_edit(
        self,
        *,
        package_name: str,
        track: str,
        version_code: int,
    ) -> bool:
        return await self._client.reconcile_version(
            package_name=package_name,
            track=track,
            version_code=version_code,
        )

    async def submit(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> str:
        package_name, track = self._validate_target(target)
        if operation_id is None or not operation_id.strip():
            raise GoogleVendorError(
                "GOOGLE_EDIT_ID_MISSING",
                "Google Play submission requires the persisted App Edit ID.",
                ExitCode.LOCAL_STATE,
            )
        try:
            version_code = int(artifact_id)
        except ValueError:
            version_code = 0
        if version_code <= 0:
            raise GoogleVendorError(
                "GOOGLE_VERSION_CODE_INVALID",
                "Google Play submission requires a positive version code.",
                ExitCode.LOCAL_STATE,
            )
        normalized_artifact_id = str(version_code)
        edit_id = operation_id.strip()

        if await self._client.is_version_visible(
            package_name=package_name,
            track=track,
            version_code=version_code,
        ):
            return normalized_artifact_id

        try:
            await self._client.get_edit(package_name=package_name, edit_id=edit_id)
            await self._client.validate_edit(package_name=package_name, edit_id=edit_id)
        except GoogleVendorError as error:
            if error.status_code != 404:
                raise
            if await self._reconcile_missing_edit(
                package_name=package_name,
                track=track,
                version_code=version_code,
            ):
                return normalized_artifact_id
            raise self._expired_edit_error() from None

        try:
            await self._client.commit_edit(package_name=package_name, edit_id=edit_id)
        except GoogleVendorError as error:
            if error.vendor_code == "FAILED_PRECONDITION":
                raise GoogleVendorError(
                    "GOOGLE_CHANGES_IN_REVIEW",
                    "Google Play already has changes in review; wait for that review to finish "
                    "and resume this run.",
                    ExitCode.VENDOR_REJECTION,
                    resumable=True,
                    vendor_code=error.vendor_code,
                    status_code=error.status_code,
                ) from None
            should_reconcile = (
                error.resumable
                or error.status_code in {404, 409}
                or error.code == "GOOGLE_RESPONSE_INVALID"
            )
            if not should_reconcile:
                raise
            try:
                visible = await self._reconcile_missing_edit(
                    package_name=package_name,
                    track=track,
                    version_code=version_code,
                )
            except GoogleVendorError:
                raise error from None
            if visible:
                return normalized_artifact_id
            if error.status_code == 404:
                raise self._expired_edit_error() from None
            if error.status_code == 409:
                raise GoogleVendorError(
                    "GOOGLE_EDIT_CONFLICT",
                    "Google Play rejected the App Edit because of a concurrent update; "
                    "start a new publish.",
                    ExitCode.LOCAL_STATE,
                    resumable=False,
                    vendor_code=error.vendor_code,
                    status_code=409,
                ) from None
            raise
        return normalized_artifact_id

    async def review_status(self, *, target: StoreTarget) -> ReviewStatus:
        package_name, track = self._validate_target(target)
        payload = await self._client.list_releases(
            package_name=package_name,
            track=track,
        )
        return parse_review_status(payload)
