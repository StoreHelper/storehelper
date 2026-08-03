"""Credential resolution from CI, the operating-system keyring, or a secure prompt."""

from __future__ import annotations

import getpass
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

import keyring
from keyring.errors import KeyringError
from pydantic import ValidationError

from storehelper.credentials.models import (
    AppleApiKey,
    CredentialError,
    GoogleServiceAccount,
    HuaweiServiceAccount,
    StoreCredential,
)
from storehelper.stores.models import CredentialKind

_PROFILE_INDEX = "__profiles__"


def _service_name(kind: CredentialKind) -> str:
    names = {
        CredentialKind.HUAWEI_SERVICE_ACCOUNT: "huawei",
        CredentialKind.APPLE_API_KEY: "apple",
        CredentialKind.GOOGLE_SERVICE_ACCOUNT: "google_play",
    }
    return f"storehelper:{names[kind]}"


class KeyringStore(Protocol):
    def get(
        self,
        profile: str,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> str | None: ...

    def set(
        self,
        profile: str,
        value: str,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> None: ...

    def delete(
        self,
        profile: str,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> None: ...

    def list_profiles(
        self,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> list[str]: ...


class PromptBackend(Protocol):
    def prompt(self, kind: CredentialKind) -> StoreCredential: ...


class MemoryKeyring:
    """In-memory keyring used by tests and embedders."""

    def __init__(self) -> None:
        self._values: dict[tuple[CredentialKind, str], str] = {}

    def get(
        self,
        profile: str,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> str | None:
        return self._values.get((kind, profile))

    def set(
        self,
        profile: str,
        value: str,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> None:
        self._values[(kind, profile)] = value

    def delete(
        self,
        profile: str,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> None:
        self._values.pop((kind, profile), None)

    def list_profiles(
        self,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> list[str]:
        return sorted(profile for stored_kind, profile in self._values if stored_kind is kind)


class SystemKeyring:
    """Small safe wrapper over Python keyring with a non-secret profile index."""

    def get(
        self,
        profile: str,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> str | None:
        try:
            return keyring.get_password(_service_name(kind), profile)
        except KeyringError as error:
            raise CredentialError(
                "KEYRING_UNAVAILABLE", f"System keyring failed: {error}"
            ) from None

    def set(
        self,
        profile: str,
        value: str,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> None:
        profiles = set(self.list_profiles(kind))
        profiles.add(profile)
        try:
            service = _service_name(kind)
            keyring.set_password(service, profile, value)
            keyring.set_password(service, _PROFILE_INDEX, json.dumps(sorted(profiles)))
        except KeyringError as error:
            raise CredentialError(
                "KEYRING_UNAVAILABLE", f"System keyring failed: {error}"
            ) from None

    def delete(
        self,
        profile: str,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> None:
        profiles = set(self.list_profiles(kind))
        try:
            service = _service_name(kind)
            if self.get(profile, kind) is not None:
                keyring.delete_password(service, profile)
            profiles.discard(profile)
            keyring.set_password(service, _PROFILE_INDEX, json.dumps(sorted(profiles)))
        except KeyringError as error:
            raise CredentialError(
                "KEYRING_UNAVAILABLE", f"System keyring failed: {error}"
            ) from None

    def list_profiles(
        self,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> list[str]:
        try:
            raw = keyring.get_password(_service_name(kind), _PROFILE_INDEX)
        except KeyringError as error:
            raise CredentialError(
                "KEYRING_UNAVAILABLE", f"System keyring failed: {error}"
            ) from None
        if raw is None:
            return []
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            raise CredentialError(
                "KEYRING_INVALID", "System keyring profile index is invalid."
            ) from None
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise CredentialError("KEYRING_INVALID", "System keyring profile index is invalid.")
        return sorted(set(values))


class SecurePrompt:
    def prompt(
        self,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> StoreCredential:
        if kind is CredentialKind.APPLE_API_KEY:
            key_type = input("Apple key type (team/individual): ").strip()
            issuer_id = input("Apple issuer_id (empty for individual): ").strip() or None
            return _parse_credential(
                {
                    "key_type": key_type,
                    "key_id": input("Apple key_id: ").strip(),
                    "issuer_id": issuer_id,
                    "private_key": getpass.getpass("Apple private_key (paste with \\n): "),
                },
                kind,
            )
        if kind is CredentialKind.GOOGLE_SERVICE_ACCOUNT:
            values = {
                "type": "service_account",
                "project_id": input("Google project_id: ").strip(),
                "private_key_id": input("Google private_key_id: ").strip(),
                "client_email": input("Google client_email: ").strip(),
                "private_key": getpass.getpass("Google private_key (paste with \\n): "),
            }
            token_uri = input(
                "Google token_uri (empty for https://oauth2.googleapis.com/token): "
            ).strip()
            if token_uri:
                values["token_uri"] = token_uri
            return _parse_credential(values, kind)
        return _parse_credential(
            {
                "key_id": input("Huawei key_id: ").strip(),
                "sub_account": input("Huawei sub_account: ").strip(),
                "private_key": getpass.getpass("Huawei private_key (paste with \\n): "),
            },
            kind,
        )


def _parse_account(value: object) -> HuaweiServiceAccount:
    try:
        return HuaweiServiceAccount.model_validate(value)
    except CredentialError:
        raise
    except ValidationError:
        raise CredentialError(
            "CREDENTIAL_INVALID",
            "Huawei Service Account must contain key_id, sub_account, and private_key.",
        ) from None


def _parse_credential(value: object, kind: CredentialKind) -> StoreCredential:
    if kind is CredentialKind.HUAWEI_SERVICE_ACCOUNT:
        return _parse_account(value)
    if kind is CredentialKind.GOOGLE_SERVICE_ACCOUNT:
        try:
            return GoogleServiceAccount.model_validate(value)
        except CredentialError:
            raise
        except ValidationError:
            raise CredentialError(
                "CREDENTIAL_INVALID",
                "Google service-account JSON is missing or contains invalid required fields.",
            ) from None
    try:
        return AppleApiKey.model_validate(value)
    except CredentialError:
        raise
    except ValidationError:
        raise CredentialError(
            "CREDENTIAL_INVALID",
            "Apple API key must contain key_type, key_id, private_key, and the correct issuer_id.",
        ) from None


def parse_stored_credential(value: object, kind: CredentialKind) -> StoreCredential:
    if not isinstance(value, Mapping):
        raise CredentialError("CREDENTIAL_INVALID", "Stored credential must be a JSON object.")
    stored_kind = value.get("credential_kind")
    if stored_kind is None:
        if kind is not CredentialKind.HUAWEI_SERVICE_ACCOUNT:
            raise CredentialError(
                "CREDENTIAL_KIND_MISMATCH",
                "The stored credential belongs to a different store.",
            )
        return _parse_account(value)
    if stored_kind != kind.value:
        raise CredentialError(
            "CREDENTIAL_KIND_MISMATCH",
            "The stored credential belongs to a different store.",
        )
    return _parse_credential(value.get("credential"), kind)


def load_service_account_file(path: Path) -> HuaweiServiceAccount:
    credential = load_credential_file(path, CredentialKind.HUAWEI_SERVICE_ACCOUNT)
    assert isinstance(credential, HuaweiServiceAccount)
    return credential


def load_credential_file(path: Path, kind: CredentialKind) -> StoreCredential:
    if not path.is_file():
        raise CredentialError("CREDENTIAL_FILE_NOT_FOUND", f"Credential file not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise CredentialError(
            "CREDENTIAL_FILE_INVALID",
            "Credential file is not readable JSON.",
        ) from None
    return _parse_credential(value, kind)


class CredentialProvider:
    def __init__(
        self,
        keyring_store: KeyringStore,
        *,
        environment: Mapping[str, str] | None = None,
        prompt: PromptBackend | None = None,
    ) -> None:
        self._keyring = keyring_store
        self._environment = os.environ if environment is None else environment
        self._prompt = prompt or SecurePrompt()

    def resolve(
        self,
        profile: str,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
        *,
        interactive: bool,
    ) -> StoreCredential:
        if kind is CredentialKind.APPLE_API_KEY:
            return self._resolve_apple(profile, interactive=interactive)
        if kind is CredentialKind.GOOGLE_SERVICE_ACCOUNT:
            return self._resolve_google(profile, interactive=interactive)
        return self._resolve_huawei(profile, interactive=interactive)

    def _resolve_huawei(self, profile: str, *, interactive: bool) -> HuaweiServiceAccount:
        individual_names = (
            "STOREHELPER_HUAWEI_KEY_ID",
            "STOREHELPER_HUAWEI_SUB_ACCOUNT",
            "STOREHELPER_HUAWEI_PRIVATE_KEY",
        )
        file_value = self._environment.get("STOREHELPER_HUAWEI_CREDENTIALS_FILE")
        individual_values = [self._environment.get(name) for name in individual_names]
        if file_value and any(value is not None for value in individual_values):
            raise CredentialError(
                "CREDENTIAL_SOURCE_CONFLICT",
                "Use either STOREHELPER_HUAWEI_CREDENTIALS_FILE or individual Huawei values.",
            )
        if file_value:
            return load_service_account_file(Path(file_value))
        if any(value is not None for value in individual_values):
            if not all(value for value in individual_values):
                raise CredentialError(
                    "CREDENTIAL_ENV_INCOMPLETE",
                    "All Huawei Service Account environment values are required.",
                )
            return _parse_account(
                {
                    "key_id": individual_values[0],
                    "sub_account": individual_values[1],
                    "private_key": individual_values[2],
                }
            )

        stored = self._keyring.get(profile, CredentialKind.HUAWEI_SERVICE_ACCOUNT)
        if stored is not None:
            try:
                parsed = parse_stored_credential(
                    json.loads(stored), CredentialKind.HUAWEI_SERVICE_ACCOUNT
                )
                assert isinstance(parsed, HuaweiServiceAccount)
                return parsed
            except json.JSONDecodeError:
                raise CredentialError(
                    "CREDENTIAL_INVALID",
                    f"Credential profile is invalid: {profile}",
                ) from None
        if interactive:
            prompted = self._prompt.prompt(CredentialKind.HUAWEI_SERVICE_ACCOUNT)
            assert isinstance(prompted, HuaweiServiceAccount)
            return prompted
        raise CredentialError(
            "CREDENTIAL_NOT_FOUND",
            f"Huawei credential profile is not available: {profile}",
        )

    def _resolve_apple(self, profile: str, *, interactive: bool) -> AppleApiKey:
        names = (
            "STOREHELPER_APPLE_KEY_TYPE",
            "STOREHELPER_APPLE_KEY_ID",
            "STOREHELPER_APPLE_ISSUER_ID",
            "STOREHELPER_APPLE_PRIVATE_KEY",
        )
        file_value = self._environment.get("STOREHELPER_APPLE_CREDENTIALS_FILE")
        values = [self._environment.get(name) for name in names]
        if file_value and any(value is not None for value in values):
            raise CredentialError(
                "CREDENTIAL_SOURCE_CONFLICT",
                "Use either STOREHELPER_APPLE_CREDENTIALS_FILE or individual Apple values.",
            )
        if file_value:
            credential = load_credential_file(Path(file_value), CredentialKind.APPLE_API_KEY)
            assert isinstance(credential, AppleApiKey)
            return credential
        if any(value is not None for value in values):
            key_type, key_id, issuer_id, private_key = values
            required = (key_type, key_id, private_key)
            if not all(required):
                raise CredentialError(
                    "CREDENTIAL_ENV_INCOMPLETE",
                    "Apple key type, key ID, and private key environment values are required.",
                )
            credential = _parse_credential(
                {
                    "key_type": key_type,
                    "key_id": key_id,
                    "issuer_id": issuer_id,
                    "private_key": private_key,
                },
                CredentialKind.APPLE_API_KEY,
            )
            assert isinstance(credential, AppleApiKey)
            return credential

        stored = self._keyring.get(profile, CredentialKind.APPLE_API_KEY)
        if stored is not None:
            try:
                credential = parse_stored_credential(
                    json.loads(stored), CredentialKind.APPLE_API_KEY
                )
            except json.JSONDecodeError:
                raise CredentialError(
                    "CREDENTIAL_INVALID",
                    f"Credential profile is invalid: {profile}",
                ) from None
            assert isinstance(credential, AppleApiKey)
            return credential
        if interactive:
            credential = self._prompt.prompt(CredentialKind.APPLE_API_KEY)
            assert isinstance(credential, AppleApiKey)
            return credential
        raise CredentialError(
            "CREDENTIAL_NOT_FOUND",
            f"Apple credential profile is not available: {profile}",
        )

    def _resolve_google(self, profile: str, *, interactive: bool) -> GoogleServiceAccount:
        names = (
            "STOREHELPER_GOOGLE_PROJECT_ID",
            "STOREHELPER_GOOGLE_PRIVATE_KEY_ID",
            "STOREHELPER_GOOGLE_PRIVATE_KEY",
            "STOREHELPER_GOOGLE_CLIENT_EMAIL",
            "STOREHELPER_GOOGLE_TOKEN_URI",
        )
        file_value = self._environment.get("STOREHELPER_GOOGLE_CREDENTIALS_FILE")
        values = [self._environment.get(name) for name in names]
        if file_value and any(value is not None for value in values):
            raise CredentialError(
                "CREDENTIAL_SOURCE_CONFLICT",
                "Use either STOREHELPER_GOOGLE_CREDENTIALS_FILE or individual Google values.",
            )
        if file_value:
            credential = load_credential_file(
                Path(file_value), CredentialKind.GOOGLE_SERVICE_ACCOUNT
            )
            assert isinstance(credential, GoogleServiceAccount)
            return credential
        if any(value is not None for value in values):
            project_id, private_key_id, private_key, client_email, token_uri = values
            if not all((project_id, private_key_id, private_key, client_email)):
                raise CredentialError(
                    "CREDENTIAL_ENV_INCOMPLETE",
                    "Google project ID, private key ID, private key, and client email "
                    "environment values are required.",
                )
            raw_credential = {
                "type": "service_account",
                "project_id": project_id,
                "private_key_id": private_key_id,
                "private_key": private_key,
                "client_email": client_email,
            }
            if token_uri:
                raw_credential["token_uri"] = token_uri
            credential = _parse_credential(raw_credential, CredentialKind.GOOGLE_SERVICE_ACCOUNT)
            assert isinstance(credential, GoogleServiceAccount)
            return credential

        stored = self._keyring.get(profile, CredentialKind.GOOGLE_SERVICE_ACCOUNT)
        if stored is not None:
            try:
                credential = parse_stored_credential(
                    json.loads(stored), CredentialKind.GOOGLE_SERVICE_ACCOUNT
                )
            except json.JSONDecodeError:
                raise CredentialError(
                    "CREDENTIAL_INVALID",
                    f"Credential profile is invalid: {profile}",
                ) from None
            assert isinstance(credential, GoogleServiceAccount)
            return credential
        if interactive:
            credential = self._prompt.prompt(CredentialKind.GOOGLE_SERVICE_ACCOUNT)
            assert isinstance(credential, GoogleServiceAccount)
            return credential
        raise CredentialError(
            "CREDENTIAL_NOT_FOUND",
            f"Google credential profile is not available: {profile}",
        )
