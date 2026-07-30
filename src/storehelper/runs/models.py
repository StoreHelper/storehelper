"""Versioned, secret-free publishing run state."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    store: Literal["huawei"] = "huawei"
    state: RunState
    app_alias: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    package_name: str = Field(min_length=1)
    package_path: str = Field(min_length=1)
    package_sha256: str = Field(min_length=1)
    logical_name: str = Field(min_length=1)
    pkg_version: str | None = None
    language: str = Field(min_length=1)
    release_notes: str | None = None
    submit: bool = True

    @property
    def resumable(self) -> bool:
        return self.state in RESUMABLE_STATES
