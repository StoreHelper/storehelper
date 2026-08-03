"""Typed Google Play publishing adapter."""

from __future__ import annotations

from collections.abc import Mapping

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.google_play.client import GooglePlayClient
from storehelper.stores.google_play.errors import GoogleVendorError
from storehelper.stores.models import (
    ProcessingState,
    ProcessingStatus,
    ReviewStatus,
    StoreName,
    StoreTarget,
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

    async def review_status(self, *, target: StoreTarget) -> ReviewStatus:
        package_name, track = self._validate_target(target)
        payload = await self._client.list_releases(
            package_name=package_name,
            track=track,
        )
        return parse_review_status(payload)
