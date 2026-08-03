"""Immutable public OPPO application and upload values."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class OppoApplicationInfo(BaseModel):
    """Only existing listing fields required for a safe version update."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    package_name: str = Field(min_length=1)
    version_code: int = Field(gt=0)
    audit_status: int = Field(ge=0)
    app_name: str = Field(min_length=1)
    second_category_id: str = Field(min_length=1)
    third_category_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    detail_desc: str = Field(min_length=1)
    privacy_source_url: str = Field(min_length=1)
    icon_url: str = Field(min_length=1)
    pic_url: str = Field(min_length=1)
    age_level: str = Field(min_length=1)
    adaptive_equipment: str = Field(min_length=1)
    copyright_url: str = Field(min_length=1)
    business_username: str = Field(min_length=1)
    business_email: str = Field(min_length=1)
    business_mobile: str = Field(min_length=1)


class OppoUploadTarget(BaseModel):
    """One vendor-issued upload URL/sign pair retained only in memory."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    upload_url: SecretStr
    upload_sign: SecretStr

    @model_validator(mode="before")
    @classmethod
    def validate_secrets(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for name in ("upload_url", "upload_sign"):
            raw = normalized.get(name)
            if isinstance(raw, SecretStr):
                raw = raw.get_secret_value()
            if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > 4096:
                raise ValueError("OPPO upload allocation is incomplete.")
            normalized[name] = raw.strip()
        return normalized


class OppoUploadedApk(BaseModel):
    """Opaque uploaded file reference plus public package MD5."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    file_url: SecretStr
    md5: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")

    @model_validator(mode="before")
    @classmethod
    def validate_file_url(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        raw = normalized.get("file_url")
        if isinstance(raw, SecretStr):
            raw = raw.get_secret_value()
        if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > 4096:
            raise ValueError("OPPO upload result is incomplete.")
        normalized["file_url"] = raw.strip()
        return normalized
