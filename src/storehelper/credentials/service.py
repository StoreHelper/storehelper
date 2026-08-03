"""Explicit credential management operations used by CLI commands."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from storehelper.credentials.models import StoreCredential
from storehelper.credentials.providers import (
    KeyringStore,
    load_credential_file,
    parse_stored_credential,
)
from storehelper.stores.models import CredentialKind

CredentialVerifier = Callable[[StoreCredential], Awaitable[bool]]


class CredentialService:
    def __init__(self, keyring_store: KeyringStore) -> None:
        self._keyring = keyring_store

    def import_file(
        self,
        profile: str,
        path: Path,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> str:
        account = load_credential_file(path, kind)
        self._keyring.set(profile, account.to_storage_json(), kind)
        return profile

    def list_profiles(
        self,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> list[str]:
        return self._keyring.list_profiles(kind)

    async def verify(
        self,
        profile: str,
        verifier: CredentialVerifier,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> bool:
        raw = self._keyring.get(profile, kind)
        if raw is None:
            return False
        account = parse_stored_credential(json.loads(raw), kind)
        return await verifier(account)

    def delete(
        self,
        profile: str,
        kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    ) -> None:
        self._keyring.delete(profile, kind)
