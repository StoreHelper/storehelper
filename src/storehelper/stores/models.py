"""Store-neutral publishing contracts."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StoreName(StrEnum):
    HUAWEI = "huawei"
    HARMONYOS = "harmonyos"
    APPLE = "apple"
    GOOGLE_PLAY = "google_play"
    XIAOMI = "xiaomi"
    OPPO = "oppo"
    VIVO = "vivo"
    HONOR = "honor"


class CredentialKind(StrEnum):
    HUAWEI_SERVICE_ACCOUNT = "huawei_service_account"
    APPLE_API_KEY = "apple_api_key"
    GOOGLE_SERVICE_ACCOUNT = "google_service_account"
    XIAOMI_API = "xiaomi_api"
    OPPO_API = "oppo_api"
    VIVO_API = "vivo_api"
    HONOR_API = "honor_api"


class StoreCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    credential_kind: CredentialKind
    artifact_suffixes: tuple[str, ...] = Field(min_length=1)
    requires_processing_poll: bool
    requires_release_notes: bool
    supports_review_status: bool
    atomic_submission: bool = False
    staged_submission: bool = False
    supports_no_submit: bool = True

    @model_validator(mode="after")
    def validate_staged_submission(self) -> StoreCapabilities:
        if self.staged_submission and not self.atomic_submission:
            raise ValueError("staged submission must use atomic uncertainty handling")
        if self.staged_submission and self.supports_no_submit:
            raise ValueError("staged submission cannot support upload-only publishing")
        return self


class StoreTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    store: StoreName
    label: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    package_name: str = Field(min_length=1)
    credential_profile: str = Field(min_length=1)
    language: str = Field(min_length=1)
    release_id: str | None = Field(default=None, min_length=1)
    platform: str | None = Field(default=None, min_length=1)
    track: str | None = Field(default=None, min_length=1)
    release_status: str | None = Field(default=None, min_length=1)
    app_name: str | None = Field(default=None, min_length=1)
    icon_path: Path | None = None
    privacy_url: str | None = Field(default=None, min_length=1)
    version_code: int | None = Field(default=None, gt=0)


class VerifiedApplication(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: str = Field(min_length=1)
    package_name: str | None = None


class UploadedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1)
    operation_id: str | None = Field(default=None, min_length=1)


class ProcessingState(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class ProcessingStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: ProcessingState
    reason: str | None = None
    artifact_id: str | None = Field(default=None, min_length=1)


class ReviewStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    IN_REVIEW = "in_review"
    PENDING_REVIEW = "pending_review"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"
