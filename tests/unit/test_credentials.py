from __future__ import annotations

import json
from pathlib import Path

import pytest

from storehelper.credentials.models import HuaweiServiceAccount
from storehelper.credentials.providers import CredentialError, CredentialProvider, MemoryKeyring
from storehelper.credentials.service import CredentialService


def account_json(private_key: str, *, key_id: str = "kid-1") -> str:
    return json.dumps(
        {
            "key_id": key_id,
            "sub_account": "sub-1",
            "private_key": private_key,
        }
    )


@pytest.mark.usefixtures("clean_huawei_env")
def test_environment_credentials_win_over_keyring(
    monkeypatch: pytest.MonkeyPatch,
    rsa_private_key: str,
) -> None:
    keyring = MemoryKeyring()
    keyring.set("company", account_json(rsa_private_key, key_id="keyring-kid"))
    monkeypatch.setenv("STOREHELPER_HUAWEI_KEY_ID", "env-kid")
    monkeypatch.setenv("STOREHELPER_HUAWEI_SUB_ACCOUNT", "env-sub")
    monkeypatch.setenv("STOREHELPER_HUAWEI_PRIVATE_KEY", rsa_private_key)

    resolved = CredentialProvider(keyring).resolve("company", interactive=False)

    assert resolved.key_id == "env-kid"
    assert resolved.sub_account == "env-sub"


@pytest.mark.usefixtures("clean_huawei_env")
def test_secret_file_conflicts_with_individual_environment_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    path = tmp_path / "service-account.json"
    path.write_text(account_json(rsa_private_key), encoding="utf-8")
    monkeypatch.setenv("STOREHELPER_HUAWEI_CREDENTIALS_FILE", str(path))
    monkeypatch.setenv("STOREHELPER_HUAWEI_KEY_ID", "duplicate")

    with pytest.raises(CredentialError) as raised:
        CredentialProvider(MemoryKeyring()).resolve("company", interactive=False)

    assert raised.value.code == "CREDENTIAL_SOURCE_CONFLICT"


@pytest.mark.usefixtures("clean_huawei_env")
def test_reads_named_keyring_profile(rsa_private_key: str) -> None:
    keyring = MemoryKeyring()
    keyring.set("company", account_json(rsa_private_key))

    resolved = CredentialProvider(keyring).resolve("company", interactive=False)

    assert resolved.key_id == "kid-1"


@pytest.mark.usefixtures("clean_huawei_env")
def test_noninteractive_missing_credentials_fails() -> None:
    with pytest.raises(CredentialError) as raised:
        CredentialProvider(MemoryKeyring()).resolve("missing", interactive=False)

    assert raised.value.code == "CREDENTIAL_NOT_FOUND"


def test_service_account_repr_and_dump_never_contain_private_key(rsa_private_key: str) -> None:
    account = HuaweiServiceAccount.model_validate_json(account_json(rsa_private_key))

    assert rsa_private_key not in repr(account)
    assert rsa_private_key not in str(account.model_dump())
    assert account.private_key.get_secret_value() == rsa_private_key


def test_literal_newlines_are_normalized(rsa_private_key: str) -> None:
    escaped = rsa_private_key.replace("\n", "\\n")

    account = HuaweiServiceAccount.model_validate_json(account_json(escaped))

    assert account.private_key.get_secret_value() == rsa_private_key


def test_invalid_private_key_error_does_not_echo_value() -> None:
    invalid = "very-secret-but-not-a-private-key"

    with pytest.raises(CredentialError) as raised:
        HuaweiServiceAccount.model_validate(
            {"key_id": "kid", "sub_account": "sub", "private_key": invalid}
        )

    assert invalid not in str(raised.value)


def test_import_lists_and_deletes_profile(tmp_path: Path, rsa_private_key: str) -> None:
    source = tmp_path / "service-account.json"
    source.write_text(account_json(rsa_private_key), encoding="utf-8")
    keyring = MemoryKeyring()
    service = CredentialService(keyring)

    imported = service.import_file("company", source)

    assert imported == "company"
    assert service.list_profiles() == ["company"]
    assert rsa_private_key not in keyring.list_profiles()[0]
    service.delete("company")
    assert service.list_profiles() == []
