"""Local artifact values shared by publishing adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ArtifactInfo(BaseModel):
    """Validated, secret-free information about a local publishing artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    kind: str = Field(min_length=1)
    size: int = Field(gt=0)
    sha256: str = Field(min_length=64, max_length=64)
    logical_name: str = Field(min_length=1)
    warnings: tuple[str, ...] = ()


class AppleArtifactInfo(ArtifactInfo):
    """Locally inspected values from the primary iOS application in an IPA."""

    bundle_id: str = Field(min_length=1)
    marketing_version: str = Field(min_length=1)
    build_version: str = Field(min_length=1)
    platform: Literal["IOS"] = "IOS"
