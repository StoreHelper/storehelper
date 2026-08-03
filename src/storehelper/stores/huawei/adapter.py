"""Typed Huawei Android adapter and response normalization."""

from __future__ import annotations

from collections.abc import Mapping

from storehelper.artifacts.models import ArtifactInfo
from storehelper.domain.errors import redact
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.huawei.client import HuaweiClient
from storehelper.stores.huawei.errors import HuaweiVendorError
from storehelper.stores.models import (
    ProcessingState,
    ProcessingStatus,
    ReviewStatus,
    StoreName,
    StoreTarget,
    UploadedArtifact,
    VerifiedApplication,
)

CompileState = ProcessingState
CompileStatus = ProcessingStatus


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _safe_reason(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        value = value.get("code") or value.get("msg") or "package compilation failed"
    reason = redact(str(value)).strip()[:500]
    return reason or None


def parse_compile_status(data: Mapping[str, object], pkg_version: str) -> CompileStatus:
    """Normalize current and legacy Huawei compile-state response shapes."""

    records = data.get("pkgStateList")
    if not isinstance(records, list):
        return CompileStatus(state=CompileState.PROCESSING)
    matched: Mapping[str, object] | None = None
    for record in records:
        if isinstance(record, Mapping) and str(record.get("pkgId")) == pkg_version:
            matched = record
            break
    if matched is None:
        return CompileStatus(state=CompileState.PROCESSING)

    if "successStatus" in matched:
        status = _as_int(matched.get("successStatus"))
        if status == 0:
            return CompileStatus(state=CompileState.READY)
        if status == 1:
            return CompileStatus(state=CompileState.PROCESSING)
        if status == 2:
            return CompileStatus(
                state=CompileState.FAILED,
                reason=_safe_reason(matched.get("failReason")),
            )
        raise HuaweiVendorError(
            "HUAWEI_COMPILE_STATUS_UNKNOWN",
            "Huawei returned an unknown compile status.",
            ExitCode.VENDOR_REJECTION,
        )

    legacy = _as_int(matched.get("aabCompileStatus"))
    if legacy in (None, 0, 1):
        return CompileStatus(state=CompileState.PROCESSING)
    if legacy == 2:
        return CompileStatus(state=CompileState.READY)
    return CompileStatus(
        state=CompileState.FAILED,
        reason=_safe_reason(matched.get("failReason")),
    )


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


class HuaweiAndroidAdapter:
    """Expose only the operations required by StoreHelper's publishing service."""

    def __init__(self, client: HuaweiClient) -> None:
        self._client = client

    async def verify(self, *, target: StoreTarget) -> VerifiedApplication:
        return await self._client.verify_app(
            app_id=target.app_id,
            package_name=target.package_name,
        )

    async def upload(
        self,
        *,
        target: StoreTarget,
        artifact: ArtifactInfo | None = None,
        package: ArtifactInfo | None = None,
    ) -> UploadedArtifact:
        """Upload an artifact, accepting the schema-v1 keyword during migration."""

        selected = artifact if artifact is not None else package
        if selected is None:
            raise ValueError("artifact is required")
        return await self._client.upload_and_bind(app_id=target.app_id, package=selected)

    async def processing_status(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> ProcessingStatus:
        data = await self._client.request_json(
            "GET",
            "package/compile/status",
            params={"appId": target.app_id, "pkgIds": artifact_id},
        )
        return parse_compile_status(data, artifact_id)

    async def compile_status(self, *, app_id: str, pkg_version: str) -> CompileStatus:
        """Compatibility shim for the schema-v1 publisher."""

        target = StoreTarget(
            store=StoreName.HUAWEI,
            label="Huawei AppGallery (Android)",
            app_id=app_id,
            package_name="compatibility.placeholder",
            credential_profile="compatibility",
            language="zh-CN",
        )
        return await self.processing_status(target=target, artifact_id=pkg_version)

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
                "HUAWEI_RELEASE_NOTES_INVALID",
                "Huawei release notes must contain 1 to 500 characters.",
                ExitCode.VENDOR_REJECTION,
            )
        await self._client.request_json(
            "PUT",
            "app-language-info",
            params={"appId": app_id, "releaseType": "1"},
            json={"lang": language, "newFeatures": notes},
        )

    async def prepare_release(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
        release_notes: str | None,
    ) -> None:
        if release_notes is None:
            return
        await self.update_release_notes(
            app_id=target.app_id,
            language=target.language,
            release_notes=release_notes,
        )

    async def submit(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> str:
        await self._client.request_json(
            "POST",
            "app-submit",
            params={"appId": target.app_id, "releaseType": "1"},
        )
        return target.app_id

    async def review_status(self, *, target: StoreTarget) -> ReviewStatus:
        data = await self._client.request_json(
            "GET",
            "app-info",
            params={"appId": target.app_id, "releaseType": "1"},
        )
        app_info = data.get("appInfo")
        if not isinstance(app_info, Mapping):
            return ReviewStatus.UNKNOWN
        state = _as_int(app_info.get("releaseState"))
        if state is None:
            return ReviewStatus.UNKNOWN
        return _REVIEW_STATES.get(state, ReviewStatus.UNKNOWN)
