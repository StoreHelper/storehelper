"""Xiaomi store-neutral adapter boundary."""

from __future__ import annotations

from typing import NoReturn
from urllib.parse import urlsplit

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
from storehelper.stores.xiaomi.client import XiaomiClient
from storehelper.stores.xiaomi.errors import XiaomiVendorError
from storehelper.stores.xiaomi.package import MAX_XIAOMI_PACKAGE_SIZE, validate_xiaomi_target


class XiaomiAdapter:
    def __init__(self, client: XiaomiClient) -> None:
        self._client = client

    @staticmethod
    def _validate_target(target: StoreTarget) -> str:
        if (
            target.store is not StoreName.XIAOMI
            or target.app_id != target.package_name
            or not target.package_name.strip()
        ):
            raise XiaomiVendorError(
                "XIAOMI_TARGET_INVALID",
                "Xiaomi publishing requires one matching existing package identity.",
                ExitCode.VENDOR_REJECTION,
            )
        return target.package_name

    async def verify(self, *, target: StoreTarget) -> VerifiedApplication:
        package_name = self._validate_target(target)
        return await self._client.query_package(package_name=package_name)

    @staticmethod
    def _atomic_only() -> NoReturn:
        raise XiaomiVendorError(
            "XIAOMI_ATOMIC_ONLY",
            "Xiaomi uploads and review submission are one atomic operation.",
            ExitCode.LOCAL_STATE,
        )

    async def upload(
        self,
        *,
        target: StoreTarget,
        artifact: ArtifactInfo,
    ) -> UploadedArtifact:
        self._atomic_only()

    async def processing_status(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> ProcessingStatus:
        self._atomic_only()

    async def prepare_release(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
        release_notes: str | None,
    ) -> None:
        self._atomic_only()

    async def submit(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> str:
        self._atomic_only()

    async def review_status(self, *, target: StoreTarget) -> ReviewStatus:
        raise XiaomiVendorError(
            "XIAOMI_STATUS_UNSUPPORTED",
            "Xiaomi does not provide an automatic-publishing review-status API.",
            ExitCode.USAGE,
        )

    async def publish_atomic(
        self,
        *,
        target: StoreTarget,
        artifact: ArtifactInfo,
        release_notes: str | None,
    ) -> str:
        package_name = self._validate_target(target)
        privacy = urlsplit(target.privacy_url or "")
        if (
            target.app_name is None
            or target.privacy_url is None
            or target.icon_path is None
            or privacy.scheme != "https"
            or privacy.hostname is None
            or privacy.username is not None
            or privacy.password is not None
        ):
            raise XiaomiVendorError(
                "XIAOMI_TARGET_INVALID",
                "Xiaomi publishing requires app name, HTTPS privacy URL, and PNG icon.",
                ExitCode.VENDOR_REJECTION,
            )
        validate_xiaomi_target(target)
        if (
            artifact.kind != "apk"
            or artifact.path.suffix.lower() != ".apk"
            or artifact.size > MAX_XIAOMI_PACKAGE_SIZE
        ):
            raise XiaomiVendorError(
                "XIAOMI_PACKAGE_INVALID",
                "Xiaomi publishing requires one validated APK no larger than 2 GiB.",
                ExitCode.PACKAGE_VALIDATION,
            )
        notes = release_notes.strip() if release_notes is not None else ""
        if not 1 <= len(notes) <= 500:
            raise XiaomiVendorError(
                "XIAOMI_RELEASE_NOTES_INVALID",
                "Xiaomi release notes must contain 1 to 500 characters.",
                ExitCode.VENDOR_REJECTION,
            )
        return await self._client.push_update(
            package_name=package_name,
            app_name=target.app_name,
            privacy_url=target.privacy_url,
            icon_path=target.icon_path,
            artifact=artifact,
            release_notes=notes,
        )
