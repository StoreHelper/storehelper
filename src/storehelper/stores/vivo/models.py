"""Immutable public vivo application and transient upload values."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class VivoApplicationInfo(BaseModel):
    """Minimal public identity needed to validate a vivo update."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    package_name: str = Field(min_length=1)
    version_code: int = Field(gt=0)
    status: int = Field(ge=1, le=6)


class VivoUploadedApk(BaseModel):
    """Temporary vivo upload reference retained in adapter memory only."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    serialnumber: SecretStr
    md5: SecretStr
