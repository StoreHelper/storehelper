"""Typed App Store Connect publishing adapter."""

from __future__ import annotations

from collections.abc import Mapping

from storehelper.artifacts.models import AppleArtifactInfo, ArtifactInfo
from storehelper.domain.errors import redact
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.apple.client import AppleClient
from storehelper.stores.apple.errors import AppleVendorError
from storehelper.stores.models import (
    ProcessingState,
    ProcessingStatus,
    ReviewStatus,
    StoreTarget,
    UploadedArtifact,
    VerifiedApplication,
)


def _state_reason(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    reasons: list[str] = []
    for raw in value[:3]:
        if not isinstance(raw, Mapping):
            continue
        fields: list[str] = []
        for name in ("code", "description"):
            field = raw.get(name)
            if isinstance(field, (str, int)):
                cleaned = redact(str(field)).replace("\r", " ").replace("\n", " ").strip()
                if cleaned:
                    fields.append(cleaned[:300])
        if fields:
            reasons.append(": ".join(fields))
    return "; ".join(reasons)[:500] or None


def parse_build_upload_status(payload: Mapping[str, object]) -> ProcessingStatus:
    data = payload.get("data")
    if not isinstance(data, Mapping) or data.get("type") != "buildUploads":
        raise AppleVendorError(
            "APPLE_BUILD_UPLOAD_STATE_INVALID",
            "App Store Connect returned an invalid build upload state.",
            ExitCode.VENDOR_REJECTION,
        )
    attributes = data.get("attributes")
    state_info = attributes.get("state") if isinstance(attributes, Mapping) else None
    state = state_info.get("state") if isinstance(state_info, Mapping) else None
    if state in {"AWAITING_UPLOAD", "PROCESSING"}:
        return ProcessingStatus(state=ProcessingState.PROCESSING)
    if state == "FAILED":
        errors = state_info.get("errors") if isinstance(state_info, Mapping) else None
        return ProcessingStatus(
            state=ProcessingState.FAILED,
            reason=_state_reason(errors),
        )
    if state == "COMPLETE":
        relationships = data.get("relationships")
        build = relationships.get("build") if isinstance(relationships, Mapping) else None
        related = build.get("data") if isinstance(build, Mapping) else None
        if (
            isinstance(related, Mapping)
            and related.get("type") == "builds"
            and isinstance(related.get("id"), str)
            and related.get("id")
        ):
            return ProcessingStatus(
                state=ProcessingState.READY,
                artifact_id=str(related["id"]),
            )
        return ProcessingStatus(state=ProcessingState.PROCESSING)
    raise AppleVendorError(
        "APPLE_BUILD_UPLOAD_STATE_INVALID",
        "App Store Connect returned an unknown build upload state.",
        ExitCode.VENDOR_REJECTION,
    )


_PENDING_REVIEW_STATES = {"READY_FOR_REVIEW", "WAITING_FOR_REVIEW", "PREPARE_FOR_SUBMISSION"}
_REJECTED_STATES = {
    "DEVELOPER_REJECTED",
    "INVALID_BINARY",
    "METADATA_REJECTED",
    "REJECTED",
}
_APPROVED_STATES = {
    "ACCEPTED",
    "PENDING_APPLE_RELEASE",
    "PENDING_DEVELOPER_RELEASE",
    "PREORDER_READY_FOR_SALE",
    "PROCESSING_FOR_APP_STORE",
    "PROCESSING_FOR_DISTRIBUTION",
    "READY_FOR_DISTRIBUTION",
    "READY_FOR_SALE",
}
_SUSPENDED_STATES = {
    "DEVELOPER_REMOVED_FROM_SALE",
    "REMOVED_FROM_SALE",
    "REPLACED_WITH_NEW_VERSION",
}


def parse_review_status(payload: Mapping[str, object]) -> ReviewStatus:
    data = payload.get("data")
    if not isinstance(data, Mapping) or data.get("type") != "appStoreVersions":
        raise AppleVendorError(
            "APPLE_REVIEW_STATUS_INVALID",
            "App Store Connect returned an invalid version status.",
            ExitCode.VENDOR_REJECTION,
        )
    attributes = data.get("attributes")
    state = attributes.get("appStoreState") if isinstance(attributes, Mapping) else None
    if not isinstance(state, str):
        raise AppleVendorError(
            "APPLE_REVIEW_STATUS_INVALID",
            "App Store Connect version status is missing.",
            ExitCode.VENDOR_REJECTION,
        )
    if state in _PENDING_REVIEW_STATES:
        return ReviewStatus.PENDING_REVIEW
    if state == "IN_REVIEW":
        return ReviewStatus.IN_REVIEW
    if state in _REJECTED_STATES:
        return ReviewStatus.REJECTED
    if state in _APPROVED_STATES:
        return ReviewStatus.APPROVED
    if state in _SUSPENDED_STATES:
        return ReviewStatus.SUSPENDED
    return ReviewStatus.UNKNOWN


class AppleAdapter:
    def __init__(self, client: AppleClient) -> None:
        self._client = client
        self._verified_version_string: str | None = None

    @property
    def verified_version_string(self) -> str | None:
        return self._verified_version_string

    async def verify(self, *, target: StoreTarget) -> VerifiedApplication:
        verified = await self._client.verify_target(target=target)
        assert target.release_id is not None
        self._verified_version_string = self._client.verified_version_string(target.release_id)
        return verified

    async def upload(self, *, target: StoreTarget, artifact: ArtifactInfo) -> UploadedArtifact:
        if not isinstance(artifact, AppleArtifactInfo):
            raise AppleVendorError(
                "APPLE_PACKAGE_INVALID",
                "Apple publishing requires a validated IPA artifact.",
                ExitCode.PACKAGE_VALIDATION,
            )
        if artifact.bundle_id != target.package_name:
            raise AppleVendorError(
                "APPLE_BUNDLE_MISMATCH",
                "The IPA bundle ID does not match the configured Apple target.",
                ExitCode.PACKAGE_VALIDATION,
            )
        if self._verified_version_string != artifact.marketing_version:
            raise AppleVendorError(
                "APPLE_VERSION_MISMATCH",
                "The IPA marketing version does not match the configured App Store version.",
                ExitCode.PACKAGE_VALIDATION,
            )
        return await self._client.upload_ipa(target=target, artifact=artifact)

    async def processing_status(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> ProcessingStatus:
        payload = await self._client.build_upload_status(artifact_id)
        return parse_build_upload_status(payload)

    async def prepare_release(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
        release_notes: str | None,
    ) -> None:
        notes = release_notes.strip() if release_notes is not None else None
        if notes is not None and not 1 <= len(notes) <= 4000:
            raise AppleVendorError(
                "APPLE_RELEASE_NOTES_INVALID",
                "Apple What's New text must contain 1 to 4000 characters.",
                ExitCode.VENDOR_REJECTION,
            )
        await self._client.prepare_release(
            target=target,
            build_id=artifact_id,
            release_notes=notes,
        )

    async def submit(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> str:
        return await self._client.submit_for_review(target=target)

    async def review_status(self, *, target: StoreTarget) -> ReviewStatus:
        if target.release_id is None:
            raise AppleVendorError(
                "APPLE_TARGET_INVALID",
                "Apple target requires an App Store version ID.",
                ExitCode.VENDOR_REJECTION,
            )
        payload = await self._client.app_store_version_status(release_id=target.release_id)
        return parse_review_status(payload)
