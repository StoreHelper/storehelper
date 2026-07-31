"""Stable domain objects shared by the CLI and Python API."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PublishStage(StrEnum):
    CREATED = "created"
    VALIDATED = "validated"
    AUTHENTICATED = "authenticated"
    APP_VERIFIED = "app_verified"
    UPLOADING = "uploading"
    PACKAGE_BOUND = "package_bound"
    PACKAGE_COMPILING = "package_compiling"
    PACKAGE_READY = "package_ready"
    METADATA_UPDATED = "metadata_updated"
    SUBMITTED = "submitted"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class VendorError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str | None = None


class NextAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str


class OperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    ok: bool
    run_id: str | None = None
    store: Literal["huawei"] = "huawei"
    stage: PublishStage
    resumable: bool = False
    message: str
    vendor: VendorError | None = None
    next_action: NextAction | None = None

    @classmethod
    def success(
        cls,
        *,
        stage: PublishStage,
        run_id: str | None,
        message: str | None = None,
    ) -> OperationResult:
        if message is None:
            message = (
                "Huawei accepted the review submission."
                if stage is PublishStage.SUBMITTED
                else "Operation completed successfully."
            )
        return cls(ok=True, stage=stage, run_id=run_id, message=message)

    @classmethod
    def failure(
        cls,
        *,
        stage: PublishStage,
        run_id: str | None,
        message: str,
        resumable: bool = False,
        vendor_code: str | None = None,
    ) -> OperationResult:
        vendor = VendorError(code=vendor_code) if vendor_code is not None else None
        next_action = (
            NextAction(command=f"storehelper resume {run_id}")
            if resumable and run_id is not None
            else None
        )
        return cls(
            ok=False,
            stage=stage,
            run_id=run_id,
            resumable=resumable,
            message=message,
            vendor=vendor,
            next_action=next_action,
        )


class PublishRequest(BaseModel):
    """Application-service input independent of CLI parsing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    app_alias: str
    store: Literal["huawei"] = "huawei"
    file: Path
    release_notes: str | None = None
    submit: bool = True
    dry_run: bool = False
    confirmed: bool = False
    poll_interval_seconds: float = Field(default=15.0, ge=5.0)
    wait_timeout_seconds: float = Field(default=600.0, ge=5.0)

    @model_validator(mode="after")
    def validate_polling_window(self) -> PublishRequest:
        if self.wait_timeout_seconds < self.poll_interval_seconds:
            raise ValueError("wait timeout must be at least the poll interval")
        return self
