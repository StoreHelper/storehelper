"""Versioned, secret-free publishing run state."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from storehelper.stores.models import StoreName


class RunState(StrEnum):
    CREATED = "created"
    VALIDATED = "validated"
    APP_VERIFIED = "app_verified"
    PACKAGE_BOUND = "package_bound"
    PACKAGE_COMPILING = "package_compiling"
    PACKAGE_READY = "package_ready"
    METADATA_UPDATED = "metadata_updated"
    SUBMITTED = "submitted"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


RESUMABLE_STATES = frozenset(
    {
        RunState.CREATED,
        RunState.VALIDATED,
        RunState.APP_VERIFIED,
        RunState.PACKAGE_BOUND,
        RunState.PACKAGE_COMPILING,
        RunState.PACKAGE_READY,
        RunState.METADATA_UPDATED,
        RunState.TIMED_OUT,
        RunState.INTERRUPTED,
    }
)


class RunReceipt(BaseModel):
    """The complete allowlist of values that may be persisted for recovery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[4] = 4
    run_id: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    store: StoreName
    state: RunState
    app_alias: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    package_name: str = Field(min_length=1)
    package_path: str = Field(min_length=1)
    package_sha256: str = Field(min_length=1)
    logical_name: str = Field(min_length=1)
    artifact_id: str | None = None
    operation_id: str | None = None
    release_id: str | None = None
    submission_id: str | None = None
    track: str | None = None
    release_status: str | None = None
    language: str = Field(min_length=1)
    release_notes: str | None = None
    submit: bool = True

    @property
    def resumable(self) -> bool:
        return self.state in RESUMABLE_STATES


def migrate_receipt_payload(payload: object) -> Mapping[str, object]:
    """Convert supported legacy receipt shapes to schema version 4."""

    if not isinstance(payload, Mapping):
        raise ValueError("receipt must be a JSON object")
    version = payload.get("schema_version")
    if version == 4:
        return payload
    if version not in {1, 2, 3}:
        raise ValueError("unsupported receipt schema version")
    migrated = dict(payload)
    migrated["schema_version"] = 4
    if version == 1:
        migrated["artifact_id"] = migrated.pop("pkg_version", None)
    if version in {1, 2}:
        migrated.setdefault("release_id", None)
        migrated.setdefault("submission_id", None)
    migrated.setdefault("operation_id", None)
    migrated.setdefault("track", None)
    migrated.setdefault("release_status", None)
    return migrated
