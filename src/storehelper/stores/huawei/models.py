"""Secret-safe models for the Huawei Publishing API boundary."""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr

from storehelper.stores.models import UploadedArtifact, VerifiedApplication

HuaweiApp = VerifiedApplication


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


class BoundPackage(UploadedArtifact):
    """Compatibility type while callers migrate from `pkg_version`."""

    artifact_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("artifact_id", "pkg_version"),
    )

    @property
    def pkg_version(self) -> str:
        return self.artifact_id
