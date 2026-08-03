"""Store-neutral publishing contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StoreName(StrEnum):
    HUAWEI = "huawei"
    HARMONYOS = "harmonyos"
    APPLE = "apple"


class CredentialKind(StrEnum):
    HUAWEI_SERVICE_ACCOUNT = "huawei_service_account"
    APPLE_API_KEY = "apple_api_key"


class StoreCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    credential_kind: CredentialKind
    artifact_suffixes: tuple[str, ...] = Field(min_length=1)
    requires_processing_poll: bool
    requires_release_notes: bool
    supports_review_status: bool


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
