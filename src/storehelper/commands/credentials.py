"""Credential command helpers."""

from pathlib import Path

from storehelper.credentials.providers import KeyringStore
from storehelper.credentials.service import CredentialService
from storehelper.stores.models import CredentialKind


def import_profile(
    store: KeyringStore,
    profile: str,
    path: Path,
    kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
) -> str:
    return CredentialService(store).import_file(profile, path, kind)


def list_profiles(
    store: KeyringStore,
    kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
) -> list[str]:
    return CredentialService(store).list_profiles(kind)


def delete_profile(
    store: KeyringStore,
    profile: str,
    kind: CredentialKind = CredentialKind.HUAWEI_SERVICE_ACCOUNT,
) -> None:
    CredentialService(store).delete(profile, kind)
