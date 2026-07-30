"""Secret-safe models for the Huawei Publishing API boundary."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class HuaweiApp(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: str = Field(min_length=1)
    package_name: str | None = None


class UploadTicket(BaseModel):
    """Temporary upload values that must never enter logs or receipts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    upload_url: SecretStr
    auth_code: SecretStr
    server_file_name: str | None = None


class UploadedFile(BaseModel):
    """Internal Huawei destination returned after the binary upload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    destination: SecretStr


class BoundPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pkg_version: str = Field(min_length=1)
