"""Credential command helpers."""

from pathlib import Path

from storehelper.credentials.providers import KeyringStore
from storehelper.credentials.service import CredentialService


def import_profile(store: KeyringStore, profile: str, path: Path) -> str:
    return CredentialService(store).import_file(profile, path)


def list_profiles(store: KeyringStore) -> list[str]:
    return CredentialService(store).list_profiles()


def delete_profile(store: KeyringStore, profile: str) -> None:
    CredentialService(store).delete(profile)
