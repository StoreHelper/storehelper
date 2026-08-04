"""Typed public HONOR application and release values."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HonorLocaleInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    language_id: str = Field(min_length=1)
    app_name: str = Field(min_length=1)
    intro: str = Field(min_length=1)
    brief_intro: str | None = None
    new_feature: str | None = None


class HonorFileInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file_type: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class HonorApplicationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: int = Field(gt=0)
    package_name: str = Field(min_length=1)
    published_version_code: int = Field(gt=0)
    locales: tuple[HonorLocaleInfo, ...]
    files: tuple[HonorFileInfo, ...] = ()


class HonorCurrentRelease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: int = Field(gt=0)
    release_id: str | None = Field(default=None, min_length=1)
    version_code: int | None = Field(default=None, gt=0)
    audit_result: int = Field(ge=0)
