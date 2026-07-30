"""Secret-safe Huawei Service Account model."""

from __future__ import annotations

import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode

DEFAULT_HUAWEI_TOKEN_URI = "https://oauth-login.cloud.huawei.com/oauth2/v3/token"


class CredentialError(StoreHelperError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, ExitCode.AUTHENTICATION)


class HuaweiServiceAccount(BaseModel):
    """Huawei Service Account values whose private key stays masked by default."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    key_id: str = Field(min_length=1)
    sub_account: str = Field(min_length=1)
    private_key: SecretStr
    token_uri: str = DEFAULT_HUAWEI_TOKEN_URI

    @field_validator("key_id", "sub_account", "token_uri")
    @classmethod
    def strip_public_values(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise CredentialError("CREDENTIAL_INVALID", "Credential fields may not be empty.")
        return stripped

    @model_validator(mode="before")
    @classmethod
    def normalize_and_validate_private_key(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        raw = normalized.get("private_key")
        if isinstance(raw, SecretStr):
            raw = raw.get_secret_value()
        if not isinstance(raw, str) or not raw.strip():
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Huawei Service Account private_key is required.",
            )
        pem = raw.strip().strip('"').replace("\\r\\n", "\n").replace("\\n", "\n")
        if not pem.endswith("\n"):
            pem += "\n"
        try:
            serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        except (TypeError, ValueError):
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Huawei Service Account private_key is not a valid unencrypted PEM key.",
            ) from None
        normalized["private_key"] = pem
        return normalized

    def to_storage_json(self) -> str:
        """Serialize only for an explicitly selected secure credential backend."""

        return json.dumps(
            {
                "key_id": self.key_id,
                "sub_account": self.sub_account,
                "private_key": self.private_key.get_secret_value(),
                "token_uri": self.token_uri,
            },
            ensure_ascii=False,
        )
