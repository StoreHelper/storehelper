"""Typed HarmonyOS publishing operations."""

from __future__ import annotations

from collections.abc import Mapping

from storehelper.artifacts.models import ArtifactInfo
from storehelper.domain.errors import redact
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.harmonyos.client import HarmonyOSClient
from storehelper.stores.huawei.errors import HuaweiVendorError
from storehelper.stores.models import (
    ProcessingState,
    ProcessingStatus,
    ReviewStatus,
    StoreTarget,
    UploadedArtifact,
    VerifiedApplication,
)

_PROCESSING_STATES = {"processing", "pending", "compiling", "parsing", "1"}
_READY_STATES = {"ready", "success", "succeeded", "complete", "completed", "0"}
_FAILED_STATES = {"failed", "failure", "error", "2"}
_REVIEW_STATES = {
    0: ReviewStatus.APPROVED,
    1: ReviewStatus.REJECTED,
    4: ReviewStatus.IN_REVIEW,
    5: ReviewStatus.IN_REVIEW,
    7: ReviewStatus.PENDING_REVIEW,
    8: ReviewStatus.REJECTED,
    12: ReviewStatus.IN_REVIEW,
    13: ReviewStatus.REJECTED,
}


def _safe_reason(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        value = value.get("code") or value.get("msg") or "artifact processing failed"
    reason = redact(str(value)).strip()[:500]
    return reason or None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def parse_processing_status(data: Mapping[str, object]) -> ProcessingStatus:
    """Conservatively normalize the observed HarmonyOS package-info shapes."""

    info = data.get("packageInfo")
    if not isinstance(info, Mapping) or not info:
        return ProcessingStatus(state=ProcessingState.PROCESSING)
    raw_state: object | None = None
    for field in ("parseStatus", "compileStatus", "status"):
        if field in info:
            raw_state = info.get(field)
            break
    if raw_state is None:
        return ProcessingStatus(state=ProcessingState.READY)
    normalized = str(raw_state).strip().lower()
    if normalized in _PROCESSING_STATES:
        return ProcessingStatus(state=ProcessingState.PROCESSING)
    if normalized in _READY_STATES:
        return ProcessingStatus(state=ProcessingState.READY)
    if normalized in _FAILED_STATES:
        return ProcessingStatus(
            state=ProcessingState.FAILED,
            reason=_safe_reason(info.get("failReason") or info.get("reason")),
        )
    raise HuaweiVendorError(
        "HARMONYOS_PROCESSING_STATUS_UNKNOWN",
        "Huawei returned an unknown package processing status.",
        ExitCode.VENDOR_REJECTION,
    )


class HarmonyOSAdapter:
    def __init__(self, client: HarmonyOSClient) -> None:
        self._client = client

    async def verify(
        self,
        *,
        target: StoreTarget,
    ) -> VerifiedApplication:
        return await self._client.verify_app(
            app_id=target.app_id,
            package_name=target.package_name,
        )

    async def upload(
        self,
        *,
        target: StoreTarget,
        artifact: ArtifactInfo,
    ) -> UploadedArtifact:
        return await self._client.upload_and_bind(app_id=target.app_id, artifact=artifact)

    async def processing_status(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
    ) -> ProcessingStatus:
        data = await self._client.request_json(
            "v2",
            "GET",
            "app-package-info",
            params={"packageId": artifact_id, "appId": target.app_id},
        )
        return parse_processing_status(data)

    async def update_release_notes(
        self,
        *,
        app_id: str,
        language: str,
        release_notes: str,
    ) -> None:
        notes = release_notes.strip()
        if not 1 <= len(notes) <= 500:
            raise HuaweiVendorError(
                "HARMONYOS_RELEASE_NOTES_INVALID",
                "HarmonyOS release notes must contain 1 to 500 characters.",
                ExitCode.VENDOR_REJECTION,
            )
        await self._client.request_json(
            "v3",
            "PUT",
            "app-language-info",
            params={"appId": app_id, "releaseType": "1", "releasePhase": "0"},
            json={"lang": language, "newFeatures": notes},
        )

    async def prepare_release(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        release_notes: str | None,
    ) -> None:
        if release_notes is None:
            return
        await self.update_release_notes(
            app_id=target.app_id,
            language=target.language,
            release_notes=release_notes,
        )

    async def submit(self, *, target: StoreTarget, artifact_id: str) -> str:
        await self._client.request_json(
            "v3",
            "POST",
            "app-submit",
            params={"appId": target.app_id},
            json={"releaseType": 1, "releasePhase": 0},
        )
        return target.app_id

    async def review_status(self, *, target: StoreTarget) -> ReviewStatus:
        data = await self._client.request_json(
            "v3",
            "GET",
            "app-info",
            params={"appId": target.app_id, "releaseType": "1", "releasePhase": "0"},
        )
        app_info = data.get("appInfo")
        if not isinstance(app_info, Mapping):
            return ReviewStatus.UNKNOWN
        state = _as_int(app_info.get("releaseState"))
        if state is None:
            return ReviewStatus.UNKNOWN
        return _REVIEW_STATES.get(state, ReviewStatus.UNKNOWN)
