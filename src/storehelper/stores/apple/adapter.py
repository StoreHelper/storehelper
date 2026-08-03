"""Typed App Store Connect publishing adapter."""

from __future__ import annotations

from storehelper.artifacts.models import AppleArtifactInfo, ArtifactInfo
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.apple.client import AppleClient
from storehelper.stores.apple.errors import AppleVendorError
from storehelper.stores.models import (
    ProcessingStatus,
    ReviewStatus,
    StoreTarget,
    UploadedArtifact,
    VerifiedApplication,
)


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

    @staticmethod
    def _pending() -> AppleVendorError:
        return AppleVendorError(
            "APPLE_OPERATION_UNAVAILABLE",
            "This Apple publishing operation is not available.",
            ExitCode.USAGE,
        )

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
    ) -> ProcessingStatus:
        raise self._pending()

    async def prepare_release(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        release_notes: str | None,
    ) -> None:
        raise self._pending()

    async def submit(self, *, target: StoreTarget, artifact_id: str) -> str:
        raise self._pending()

    async def review_status(self, *, target: StoreTarget) -> ReviewStatus:
        raise self._pending()
