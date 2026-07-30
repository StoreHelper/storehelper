"""Explicit credential management operations used by CLI commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from storehelper.credentials.models import HuaweiServiceAccount
from storehelper.credentials.providers import KeyringStore, load_service_account_file

CredentialVerifier = Callable[[HuaweiServiceAccount], Awaitable[bool]]


class CredentialService:
    def __init__(self, keyring_store: KeyringStore) -> None:
        self._keyring = keyring_store

    def import_file(self, profile: str, path: Path) -> str:
        account = load_service_account_file(path)
        self._keyring.set(profile, account.to_storage_json())
        return profile

    def list_profiles(self) -> list[str]:
        return self._keyring.list_profiles()

    async def verify(self, profile: str, verifier: CredentialVerifier) -> bool:
        raw = self._keyring.get(profile)
        if raw is None:
            return False
        account = HuaweiServiceAccount.model_validate_json(raw)
        return await verifier(account)

    def delete(self, profile: str) -> None:
        self._keyring.delete(profile)
