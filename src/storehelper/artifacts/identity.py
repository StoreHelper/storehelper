"""Validated identity extracted from a publishing artifact."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

ANDROID_PACKAGE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
APPLE_BUNDLE_ID = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
_DOTTED_IDENTIFIER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+$")


class ArtifactIdentity(BaseModel):
    """Secret-free, immutable package identity for local publishing checks."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    package_name: str
    version_code: int | None = Field(default=None, gt=0)
    version_name: str | None = Field(default=None, min_length=1)

    @field_validator("package_name")
    @classmethod
    def validate_package_name(cls, value: str) -> str:
        if not _DOTTED_IDENTIFIER.fullmatch(value):
            raise ValueError("must be a dotted package or bundle identifier")
        return value
