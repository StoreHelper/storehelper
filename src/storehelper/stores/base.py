"""Store adapter protocol used by the publishing application service."""

from __future__ import annotations

from typing import Protocol

from storehelper.artifacts.models import ArtifactInfo
from storehelper.stores.models import (
    ProcessingStatus,
    ReviewStatus,
    UploadedArtifact,
    VerifiedApplication,
)


class StoreAdapter(Protocol):
    async def verify(self, *, app_id: str, package_name: str) -> VerifiedApplication: ...

    async def upload(self, *, app_id: str, artifact: ArtifactInfo) -> UploadedArtifact: ...

    async def processing_status(
        self,
        *,
        app_id: str,
        artifact_id: str,
    ) -> ProcessingStatus: ...

    async def update_release_notes(
        self,
        *,
        app_id: str,
        language: str,
        release_notes: str,
    ) -> None: ...

    async def submit(self, *, app_id: str) -> str: ...

    async def review_status(self, *, app_id: str) -> ReviewStatus: ...
