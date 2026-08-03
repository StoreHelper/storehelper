"""Store adapter protocol used by the publishing application service."""

from __future__ import annotations

from typing import Protocol

from storehelper.artifacts.models import ArtifactInfo
from storehelper.stores.models import (
    ProcessingStatus,
    ReviewStatus,
    StoreTarget,
    UploadedArtifact,
    VerifiedApplication,
)


class StoreAdapter(Protocol):
    async def verify(self, *, target: StoreTarget) -> VerifiedApplication: ...

    async def upload(self, *, target: StoreTarget, artifact: ArtifactInfo) -> UploadedArtifact: ...

    async def processing_status(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> ProcessingStatus: ...

    async def prepare_release(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
        release_notes: str | None,
    ) -> None: ...

    async def submit(
        self,
        *,
        target: StoreTarget,
        artifact_id: str,
        operation_id: str | None = None,
    ) -> str: ...

    async def review_status(self, *, target: StoreTarget) -> ReviewStatus: ...
