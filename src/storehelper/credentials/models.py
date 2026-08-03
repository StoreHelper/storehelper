"""Secret-safe Huawei Service Account model."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit

from cryptography import x509
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
_XIAOMI_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_XIAOMI_LOCALE = re.compile(r"^[a-z]{2}_[A-Z]{2}$")


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


class XiaomiReviewAccount(BaseModel):
    """One Xiaomi structured review login with masked sensitive values."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    login_type: Literal[1, 2]
    account: SecretStr | None = None
    password: SecretStr | None = None
    access_code: SecretStr | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_secret_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        unknown = set(normalized).difference({"login_type", "account", "password", "access_code"})
        if unknown:
            raise CredentialError(
                "CREDENTIAL_INVALID", "Xiaomi review account contains unsupported fields."
            )
        if normalized.get("login_type") not in (1, 2):
            raise CredentialError("CREDENTIAL_INVALID", "Xiaomi review login_type must be 1 or 2.")
        present: dict[str, str | None] = {}
        for field in ("account", "password", "access_code"):
            raw = normalized.get(field)
            if isinstance(raw, SecretStr):
                raw = raw.get_secret_value()
            if raw is None:
                present[field] = None
                continue
            if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > 50:
                raise CredentialError(
                    "CREDENTIAL_INVALID",
                    "Xiaomi review account values must contain 1 to 50 characters.",
                )
            present[field] = raw.strip()
            normalized[field] = raw.strip()
        if (present["account"] is None) != (present["password"] is None):
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Xiaomi review account and password/code must be provided together.",
            )
        if present["account"] is None and present["access_code"] is None:
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Xiaomi review account requires login values or an access code.",
            )
        return normalized

    def api_value(self) -> dict[str, object]:
        value: dict[str, object] = {"t": self.login_type}
        if self.account is not None:
            value["a"] = self.account.get_secret_value()
        if self.password is not None:
            value["p"] = self.password.get_secret_value()
        if self.access_code is not None:
            value["c"] = self.access_code.get_secret_value()
        return value

    def storage_value(self) -> dict[str, object]:
        value: dict[str, object] = {"login_type": self.login_type}
        for name in ("account", "password", "access_code"):
            secret = getattr(self, name)
            if secret is not None:
                value[name] = secret.get_secret_value()
        return value


class XiaomiReviewLocale(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    accounts: tuple[XiaomiReviewAccount, ...] = ()
    audit_notes: SecretStr | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_notes(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        unknown = set(normalized).difference({"accounts", "audit_notes"})
        if unknown:
            raise CredentialError(
                "CREDENTIAL_INVALID", "Xiaomi review locale contains unsupported fields."
            )
        raw = normalized.get("audit_notes")
        if isinstance(raw, SecretStr):
            raw = raw.get_secret_value()
        if raw is not None:
            if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > 500:
                raise CredentialError(
                    "CREDENTIAL_INVALID",
                    "Xiaomi audit notes must contain 1 to 500 characters.",
                )
            normalized["audit_notes"] = raw.strip()
        return normalized

    @model_validator(mode="after")
    def require_content(self) -> XiaomiReviewLocale:
        if len(self.accounts) > 5:
            raise CredentialError(
                "CREDENTIAL_INVALID", "Xiaomi review locales accept at most five accounts."
            )
        if not self.accounts and self.audit_notes is None:
            raise CredentialError("CREDENTIAL_INVALID", "Xiaomi review locale must not be empty.")
        return self


class XiaomiApiCredential(BaseModel):
    """Xiaomi automatic-publishing credentials and optional reviewer access."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    username: str = Field(min_length=1)
    api_secret: SecretStr
    public_key_certificate: SecretStr
    test_accounts: dict[str, XiaomiReviewLocale] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_and_validate(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        unknown = set(normalized).difference(
            {"username", "api_secret", "public_key_certificate", "test_accounts"}
        )
        if unknown:
            raise CredentialError(
                "CREDENTIAL_INVALID", "Xiaomi credential contains unsupported fields."
            )

        username = normalized.get("username")
        if not isinstance(username, str) or not _XIAOMI_EMAIL.fullmatch(username.strip()):
            raise CredentialError(
                "CREDENTIAL_INVALID", "Xiaomi username must be a developer login email."
            )
        normalized["username"] = username.strip()

        raw_secret = normalized.get("api_secret")
        if isinstance(raw_secret, SecretStr):
            raw_secret = raw_secret.get_secret_value()
        if not isinstance(raw_secret, str) or not raw_secret.strip():
            raise CredentialError("CREDENTIAL_INVALID", "Xiaomi api_secret is required.")
        normalized["api_secret"] = raw_secret.strip()

        raw_certificate = normalized.get("public_key_certificate")
        if isinstance(raw_certificate, SecretStr):
            raw_certificate = raw_certificate.get_secret_value()
        if not isinstance(raw_certificate, str) or not raw_certificate.strip():
            raise CredentialError(
                "CREDENTIAL_INVALID", "Xiaomi public_key_certificate is required."
            )
        pem = raw_certificate.strip().strip('"').replace("\\r\\n", "\n").replace("\\n", "\n")
        if not pem.endswith("\n"):
            pem += "\n"
        try:
            certificate = x509.load_pem_x509_certificate(pem.encode("utf-8"))
        except ValueError:
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Xiaomi public_key_certificate must be a valid PEM X.509 certificate.",
            ) from None
        if not isinstance(certificate.public_key(), rsa.RSAPublicKey):
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Xiaomi public_key_certificate must contain an RSA public key.",
            )
        normalized["public_key_certificate"] = pem
        return normalized

    @model_validator(mode="after")
    def validate_locale_keys(self) -> XiaomiApiCredential:
        if self.test_accounts is not None:
            if not self.test_accounts:
                raise CredentialError(
                    "CREDENTIAL_INVALID", "Xiaomi test_accounts must not be empty."
                )
            if any(not _XIAOMI_LOCALE.fullmatch(locale) for locale in self.test_accounts):
                raise CredentialError(
                    "CREDENTIAL_INVALID",
                    "Xiaomi test account locales must use language_COUNTRY form.",
                )
        return self

    def review_accounts_api_value(self) -> dict[str, object] | None:
        if self.test_accounts is None:
            return None
        result: dict[str, object] = {}
        for locale, group in self.test_accounts.items():
            value: dict[str, object] = {}
            if group.accounts:
                value["accounts"] = [account.api_value() for account in group.accounts]
            if group.audit_notes is not None:
                value["auditNotes"] = group.audit_notes.get_secret_value()
            result[locale] = value
        return result

    def to_storage_json(self) -> str:
        test_accounts: dict[str, object] | None = None
        if self.test_accounts is not None:
            test_accounts = {}
            for locale, group in self.test_accounts.items():
                value: dict[str, object] = {
                    "accounts": [account.storage_value() for account in group.accounts]
                }
                if group.audit_notes is not None:
                    value["audit_notes"] = group.audit_notes.get_secret_value()
                test_accounts[locale] = value
        return json.dumps(
            {
                "credential_kind": "xiaomi_api",
                "credential": {
                    "username": self.username,
                    "api_secret": self.api_secret.get_secret_value(),
                    "public_key_certificate": self.public_key_certificate.get_secret_value(),
                    "test_accounts": test_accounts,
                },
            },
            ensure_ascii=False,
        )


class OppoApiCredential(BaseModel):
    """Application-specific OPPO Open Platform credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    client_id: SecretStr
    client_secret: SecretStr

    @model_validator(mode="before")
    @classmethod
    def normalize_and_validate(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if set(normalized).difference({"client_id", "client_secret"}):
            raise CredentialError(
                "CREDENTIAL_INVALID", "OPPO credential contains unsupported fields."
            )
        for name in ("client_id", "client_secret"):
            raw = normalized.get(name)
            if isinstance(raw, SecretStr):
                raw = raw.get_secret_value()
            if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > 512:
                raise CredentialError(
                    "CREDENTIAL_INVALID",
                    "OPPO client_id and client_secret must contain 1 to 512 characters.",
                )
            normalized[name] = raw.strip()
        return normalized

    def to_storage_json(self) -> str:
        return json.dumps(
            {
                "credential_kind": "oppo_api",
                "credential": {
                    "client_id": self.client_id.get_secret_value(),
                    "client_secret": self.client_secret.get_secret_value(),
                },
            }
        )


class VivoApiCredential(BaseModel):
    """Account-level vivo Open Platform credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    access_key: SecretStr
    secret_key: SecretStr

    @model_validator(mode="before")
    @classmethod
    def normalize_and_validate(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if set(normalized).difference({"access_key", "secret_key"}):
            raise CredentialError(
                "CREDENTIAL_INVALID", "vivo credential contains unsupported fields."
            )
        for name in ("access_key", "secret_key"):
            raw = normalized.get(name)
            if isinstance(raw, SecretStr):
                raw = raw.get_secret_value()
            if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > 512:
                raise CredentialError(
                    "CREDENTIAL_INVALID",
                    "vivo access_key and secret_key must contain 1 to 512 characters.",
                )
            normalized[name] = raw.strip()
        return normalized

    def to_storage_json(self) -> str:
        return json.dumps(
            {
                "credential_kind": "vivo_api",
                "credential": {
                    "access_key": self.access_key.get_secret_value(),
                    "secret_key": self.secret_key.get_secret_value(),
                },
            }
        )


StoreCredential = (
    HuaweiServiceAccount
    | AppleApiKey
    | GoogleServiceAccount
    | XiaomiApiCredential
    | OppoApiCredential
    | VivoApiCredential
)
