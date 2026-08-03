"""Secret-safe Huawei Service Account model."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode

DEFAULT_HUAWEI_TOKEN_URI = "https://oauth-login.cloud.huawei.com/oauth2/v3/token"
DEFAULT_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
_GOOGLE_SERVICE_ACCOUNT_EMAIL = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9.\-]+\.gserviceaccount\.com$"
)


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
                "credential_kind": "huawei_service_account",
                "credential": {
                    "key_id": self.key_id,
                    "sub_account": self.sub_account,
                    "private_key": self.private_key.get_secret_value(),
                    "token_uri": self.token_uri,
                },
            },
            ensure_ascii=False,
        )


class AppleKeyType(StrEnum):
    TEAM = "team"
    INDIVIDUAL = "individual"


class AppleApiKey(BaseModel):
    """App Store Connect API key with an in-memory-only P-256 private key."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    key_type: AppleKeyType
    key_id: str = Field(min_length=1)
    issuer_id: str | None = None
    private_key: SecretStr

    @model_validator(mode="before")
    @classmethod
    def normalize_and_validate(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for field in ("key_id", "issuer_id"):
            raw_value = normalized.get(field)
            if isinstance(raw_value, str):
                normalized[field] = raw_value.strip() or None

        raw = normalized.get("private_key")
        if isinstance(raw, SecretStr):
            raw = raw.get_secret_value()
        if not isinstance(raw, str) or not raw.strip():
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Apple API private_key is required.",
            )
        pem = raw.strip().strip('"').replace("\\r\\n", "\n").replace("\\n", "\n")
        if not pem.endswith("\n"):
            pem += "\n"
        try:
            key: Any = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        except (TypeError, ValueError):
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Apple API private_key is not a valid unencrypted PEM key.",
            ) from None
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Apple API private_key must use the P-256 elliptic curve.",
            )
        normalized["private_key"] = pem
        return normalized

    @model_validator(mode="after")
    def validate_key_identity(self) -> AppleApiKey:
        if not self.key_id.strip():
            raise CredentialError("CREDENTIAL_INVALID", "Apple key_id may not be empty.")
        if self.key_type is AppleKeyType.TEAM and self.issuer_id is None:
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Apple team API keys require issuer_id.",
            )
        if self.key_type is AppleKeyType.INDIVIDUAL and self.issuer_id is not None:
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Apple individual API keys must not include issuer_id.",
            )
        return self

    def to_storage_json(self) -> str:
        return json.dumps(
            {
                "credential_kind": "apple_api_key",
                "credential": {
                    "key_type": self.key_type.value,
                    "key_id": self.key_id.strip(),
                    "issuer_id": self.issuer_id,
                    "private_key": self.private_key.get_secret_value(),
                },
            },
            ensure_ascii=False,
        )


class GoogleServiceAccount(BaseModel):
    """Google service-account key restricted to Android Publisher authentication."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    type: Literal["service_account"]
    project_id: str = Field(min_length=1)
    private_key_id: str = Field(min_length=1)
    private_key: SecretStr
    client_email: str = Field(min_length=1)
    token_uri: str = DEFAULT_GOOGLE_TOKEN_URI

    # Google-downloaded JSON keys contain these public metadata fields. StoreHelper accepts but
    # never persists or uses them.
    client_id: str | None = None
    auth_uri: str | None = None
    auth_provider_x509_cert_url: str | None = None
    client_x509_cert_url: str | None = None
    universe_domain: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_and_validate(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        public_fields = (
            "type",
            "project_id",
            "private_key_id",
            "client_email",
            "token_uri",
            "client_id",
            "auth_uri",
            "auth_provider_x509_cert_url",
            "client_x509_cert_url",
            "universe_domain",
        )
        unknown_fields = set(normalized).difference((*public_fields, "private_key"))
        if unknown_fields:
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Google service-account JSON contains unsupported fields.",
            )
        for field in public_fields:
            raw_value = normalized.get(field)
            if isinstance(raw_value, str):
                normalized[field] = raw_value.strip()
        if normalized.get("type") != "service_account":
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Google credential type must be service_account.",
            )

        raw = normalized.get("private_key")
        if isinstance(raw, SecretStr):
            raw = raw.get_secret_value()
        if not isinstance(raw, str) or not raw.strip():
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Google service-account private_key is required.",
            )
        pem = raw.strip().strip('"').replace("\\r\\n", "\n").replace("\\n", "\n")
        if not pem.endswith("\n"):
            pem += "\n"
        try:
            key: Any = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        except (TypeError, ValueError):
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Google service-account private_key is not a valid unencrypted PEM key.",
            ) from None
        if not isinstance(key, rsa.RSAPrivateKey):
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Google service-account private_key must be an RSA private key.",
            )
        normalized["private_key"] = pem
        return normalized

    @model_validator(mode="after")
    def validate_identity_and_token_uri(self) -> GoogleServiceAccount:
        if not _GOOGLE_SERVICE_ACCOUNT_EMAIL.fullmatch(self.client_email):
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Google client_email must be a service-account email address.",
            )
        try:
            parsed = urlsplit(self.token_uri)
            _port = parsed.port
        except ValueError:
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Google token_uri must be a valid HTTPS URL.",
            ) from None
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Google token_uri must be an HTTPS URL without credentials, query, or fragment.",
            )
        return self

    def to_storage_json(self) -> str:
        return json.dumps(
            {
                "credential_kind": "google_service_account",
                "credential": {
                    "type": self.type,
                    "project_id": self.project_id,
                    "private_key_id": self.private_key_id,
                    "private_key": self.private_key.get_secret_value(),
                    "client_email": self.client_email,
                    "token_uri": self.token_uri,
                },
            },
            ensure_ascii=False,
        )


StoreCredential = HuaweiServiceAccount | AppleApiKey | GoogleServiceAccount
