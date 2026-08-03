"""Immutable public OPPO application and upload values."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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
