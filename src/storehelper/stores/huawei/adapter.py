"""Typed Huawei Android adapter and response normalization."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from storehelper.domain.errors import redact
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.huawei.client import HuaweiClient
from storehelper.stores.huawei.errors import HuaweiVendorError
from storehelper.stores.huawei.models import BoundPackage, HuaweiApp
from storehelper.stores.huawei.package import PackageInfo


class CompileState(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class CompileStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: CompileState
    reason: str | None = None


class ReviewStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    IN_REVIEW = "in_review"
    PENDING_REVIEW = "pending_review"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"


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

    async def verify(self, *, app_id: str, package_name: str) -> HuaweiApp:
        return await self._client.verify_app(app_id=app_id, package_name=package_name)

    async def upload(self, *, app_id: str, package: PackageInfo) -> BoundPackage:
        return await self._client.upload_and_bind(app_id=app_id, package=package)

    async def compile_status(self, *, app_id: str, pkg_version: str) -> CompileStatus:
        data = await self._client.request_json(
            "GET",
            "package/compile/status",
            params={"appId": app_id, "pkgIds": pkg_version},
        )
        return parse_compile_status(data, pkg_version)

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

    async def submit(self, *, app_id: str) -> str:
        await self._client.request_json(
            "POST",
            "app-submit",
            params={"appId": app_id, "releaseType": "1"},
        )
        return app_id

    async def review_status(self, *, app_id: str) -> ReviewStatus:
        data = await self._client.request_json(
            "GET",
            "app-info",
            params={"appId": app_id, "releaseType": "1"},
        )
        app_info = data.get("appInfo")
        if not isinstance(app_info, Mapping):
            return ReviewStatus.UNKNOWN
        state = _as_int(app_info.get("releaseState"))
        if state is None:
            return ReviewStatus.UNKNOWN
        return _REVIEW_STATES.get(state, ReviewStatus.UNKNOWN)
