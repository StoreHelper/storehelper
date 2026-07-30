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

from storehelper.credentials.models import CredentialError, HuaweiServiceAccount

_SERVICE_NAME = "storehelper:huawei"
_PROFILE_INDEX = "__profiles__"


class KeyringStore(Protocol):
    def get(self, profile: str) -> str | None: ...

    def set(self, profile: str, value: str) -> None: ...

    def delete(self, profile: str) -> None: ...

    def list_profiles(self) -> list[str]: ...


class PromptBackend(Protocol):
    def prompt(self) -> HuaweiServiceAccount: ...


class MemoryKeyring:
    """In-memory keyring used by tests and embedders."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, profile: str) -> str | None:
        return self._values.get(profile)

    def set(self, profile: str, value: str) -> None:
        self._values[profile] = value

    def delete(self, profile: str) -> None:
        self._values.pop(profile, None)

    def list_profiles(self) -> list[str]:
        return sorted(self._values)


class SystemKeyring:
    """Small safe wrapper over Python keyring with a non-secret profile index."""

    def get(self, profile: str) -> str | None:
        try:
            return keyring.get_password(_SERVICE_NAME, profile)
        except KeyringError as error:
            raise CredentialError(
                "KEYRING_UNAVAILABLE", f"System keyring failed: {error}"
            ) from None

    def set(self, profile: str, value: str) -> None:
        profiles = set(self.list_profiles())
        profiles.add(profile)
        try:
            keyring.set_password(_SERVICE_NAME, profile, value)
            keyring.set_password(_SERVICE_NAME, _PROFILE_INDEX, json.dumps(sorted(profiles)))
        except KeyringError as error:
            raise CredentialError(
                "KEYRING_UNAVAILABLE", f"System keyring failed: {error}"
            ) from None

    def delete(self, profile: str) -> None:
        profiles = set(self.list_profiles())
        try:
            if self.get(profile) is not None:
                keyring.delete_password(_SERVICE_NAME, profile)
            profiles.discard(profile)
            keyring.set_password(_SERVICE_NAME, _PROFILE_INDEX, json.dumps(sorted(profiles)))
        except KeyringError as error:
            raise CredentialError(
                "KEYRING_UNAVAILABLE", f"System keyring failed: {error}"
            ) from None

    def list_profiles(self) -> list[str]:
        try:
            raw = keyring.get_password(_SERVICE_NAME, _PROFILE_INDEX)
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
    def prompt(self) -> HuaweiServiceAccount:
        values = {
            "key_id": input("Huawei key_id: ").strip(),
            "sub_account": input("Huawei sub_account: ").strip(),
            "private_key": getpass.getpass("Huawei private_key (paste with \\n): "),
        }
        return _parse_account(values)


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


def load_service_account_file(path: Path) -> HuaweiServiceAccount:
    if not path.is_file():
        raise CredentialError("CREDENTIAL_FILE_NOT_FOUND", f"Credential file not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise CredentialError(
            "CREDENTIAL_FILE_INVALID",
            "Credential file is not readable JSON.",
        ) from None
    return _parse_account(value)


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

    def resolve(self, profile: str, *, interactive: bool) -> HuaweiServiceAccount:
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

        stored = self._keyring.get(profile)
        if stored is not None:
            try:
                return _parse_account(json.loads(stored))
            except json.JSONDecodeError:
                raise CredentialError(
                    "CREDENTIAL_INVALID",
                    f"Credential profile is invalid: {profile}",
                ) from None
        if interactive:
            return self._prompt.prompt()
        raise CredentialError(
            "CREDENTIAL_NOT_FOUND",
            f"Huawei credential profile is not available: {profile}",
        )
