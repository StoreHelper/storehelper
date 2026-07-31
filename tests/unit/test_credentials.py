from __future__ import annotations

import json
from pathlib import Path

import pytest
from keyring.errors import KeyringError

from storehelper.credentials.models import HuaweiServiceAccount
from storehelper.credentials.providers import (
    CredentialError,
    CredentialProvider,
    MemoryKeyring,
    SecurePrompt,
    SystemKeyring,
    load_service_account_file,
)
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


@pytest.mark.usefixtures("clean_huawei_env")
def test_environment_credential_file_is_loaded(
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    path = tmp_path / "account.json"
    path.write_text(account_json(rsa_private_key), encoding="utf-8")
    provider = CredentialProvider(
        MemoryKeyring(),
        environment={"STOREHELPER_HUAWEI_CREDENTIALS_FILE": str(path)},
    )

    assert provider.resolve("ignored", interactive=False).key_id == "kid-1"


def test_incomplete_environment_credentials_are_rejected() -> None:
    provider = CredentialProvider(
        MemoryKeyring(),
        environment={"STOREHELPER_HUAWEI_KEY_ID": "kid"},
    )

    with pytest.raises(CredentialError) as raised:
        provider.resolve("default", interactive=False)

    assert raised.value.code == "CREDENTIAL_ENV_INCOMPLETE"


def test_invalid_stored_json_is_rejected() -> None:
    keyring = MemoryKeyring()
    keyring.set("broken", "not-json")

    with pytest.raises(CredentialError) as raised:
        CredentialProvider(keyring, environment={}).resolve("broken", interactive=False)

    assert raised.value.code == "CREDENTIAL_INVALID"


def test_interactive_provider_uses_prompt(rsa_private_key: str) -> None:
    account = HuaweiServiceAccount.model_validate_json(account_json(rsa_private_key))

    class Prompt:
        def prompt(self) -> HuaweiServiceAccount:
            return account

    resolved = CredentialProvider(
        MemoryKeyring(),
        environment={},
        prompt=Prompt(),
    ).resolve("missing", interactive=True)

    assert resolved == account


def test_secure_prompt_collects_values_without_echoing_key(
    monkeypatch: pytest.MonkeyPatch,
    rsa_private_key: str,
) -> None:
    answers = iter(["kid-1", "sub-1"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda prompt: rsa_private_key)

    account = SecurePrompt().prompt()

    assert account.key_id == "kid-1"


def test_service_account_file_failures_are_typed(tmp_path: Path) -> None:
    with pytest.raises(CredentialError) as missing:
        load_service_account_file(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("not-json", encoding="utf-8")
    with pytest.raises(CredentialError) as invalid:
        load_service_account_file(broken)

    assert missing.value.code == "CREDENTIAL_FILE_NOT_FOUND"
    assert invalid.value.code == "CREDENTIAL_FILE_INVALID"


def test_system_keyring_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    values: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        "storehelper.credentials.providers.keyring.get_password",
        lambda service, profile: values.get((service, profile)),
    )
    monkeypatch.setattr(
        "storehelper.credentials.providers.keyring.set_password",
        lambda service, profile, value: values.__setitem__((service, profile), value),
    )
    monkeypatch.setattr(
        "storehelper.credentials.providers.keyring.delete_password",
        lambda service, profile: values.pop((service, profile)),
    )
    store = SystemKeyring()

    assert store.list_profiles() == []
    store.set("work", "credential-json")
    assert store.get("work") == "credential-json"
    assert store.list_profiles() == ["work"]
    store.delete("work")
    assert store.list_profiles() == []


@pytest.mark.parametrize("raw", ["not-json", '{"profile":"wrong-shape"}', "[1]"])
def test_system_keyring_rejects_invalid_profile_index(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setattr(
        "storehelper.credentials.providers.keyring.get_password",
        lambda service, profile: raw,
    )

    with pytest.raises(CredentialError) as raised:
        SystemKeyring().list_profiles()

    assert raised.value.code == "KEYRING_INVALID"


def test_system_keyring_wraps_backend_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(service: str, profile: str) -> None:
        raise KeyringError("backend unavailable")

    monkeypatch.setattr("storehelper.credentials.providers.keyring.get_password", fail)

    with pytest.raises(CredentialError) as raised:
        SystemKeyring().get("work")

    assert raised.value.code == "KEYRING_UNAVAILABLE"


@pytest.mark.asyncio
async def test_credential_service_verify(rsa_private_key: str) -> None:
    keyring = MemoryKeyring()
    keyring.set("work", account_json(rsa_private_key))
    service = CredentialService(keyring)

    async def verifier(account: HuaweiServiceAccount) -> bool:
        return account.key_id == "kid-1"

    assert await service.verify("work", verifier) is True
    assert await service.verify("missing", verifier) is False
