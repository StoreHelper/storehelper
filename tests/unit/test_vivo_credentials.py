from __future__ import annotations

import json
from pathlib import Path

import pytest

from storehelper.credentials.models import CredentialError, VivoApiCredential
from storehelper.credentials.providers import CredentialProvider, MemoryKeyring, SecurePrompt
from storehelper.credentials.service import CredentialService
from storehelper.stores.models import CredentialKind


def vivo_value(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "access_key": "vivo-access-private-value",
        "secret_key": "vivo-secret-private-value",
    }
    value.update(updates)
    return value


def test_vivo_credential_is_bounded_secret_safe_and_serializable() -> None:
    credential = VivoApiCredential.model_validate(vivo_value())

    rendered = repr(credential) + str(credential) + str(credential.model_dump())
    stored = json.loads(credential.to_storage_json())

    assert "vivo-access-private-value" not in rendered
    assert "vivo-secret-private-value" not in rendered
    assert stored == {
        "credential_kind": "vivo_api",
        "credential": vivo_value(),
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"access_key": ""},
        {"access_key": " "},
        {"secret_key": ""},
        {"secret_key": " "},
        {"access_key": "a" * 513},
        {"secret_key": "s" * 513},
        {"unexpected": "sensitive-unsupported-value"},
    ],
)
def test_vivo_credential_rejects_invalid_values_without_echoing_them(
    updates: dict[str, object],
) -> None:
    with pytest.raises(CredentialError) as raised:
        VivoApiCredential.model_validate(vivo_value(**updates))

    rendered = str(raised.value)
    assert raised.value.code == "CREDENTIAL_INVALID"
    assert "vivo-access-private-value" not in rendered
    assert "vivo-secret-private-value" not in rendered
    assert "sensitive-unsupported-value" not in rendered


def test_vivo_friendly_file_and_independent_keyring_namespace(tmp_path: Path) -> None:
    source = tmp_path / "vivo.json"
    source.write_text(json.dumps(vivo_value()), encoding="utf-8")
    keyring = MemoryKeyring()
    service = CredentialService(keyring)

    service.import_file("wallet", source, CredentialKind.VIVO_API)
    resolved = CredentialProvider(keyring, environment={}).resolve(
        "wallet", CredentialKind.VIVO_API, interactive=False
    )

    assert isinstance(resolved, VivoApiCredential)
    assert resolved.access_key.get_secret_value() == "vivo-access-private-value"
    assert service.list_profiles(CredentialKind.VIVO_API) == ["wallet"]
    assert service.list_profiles(CredentialKind.OPPO_API) == []
    service.delete("wallet", CredentialKind.VIVO_API)
    assert service.list_profiles(CredentialKind.VIVO_API) == []


@pytest.mark.usefixtures("clean_vivo_env")
def test_vivo_complete_environment_pair_overrides_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keyring = MemoryKeyring()
    keyring.set(
        "wallet",
        VivoApiCredential.model_validate(
            vivo_value(access_key="keyring-access", secret_key="keyring-secret")
        ).to_storage_json(),
        CredentialKind.VIVO_API,
    )
    monkeypatch.setenv("STOREHELPER_VIVO_ACCESS_KEY", "environment-access")
    monkeypatch.setenv("STOREHELPER_VIVO_SECRET_KEY", "environment-secret")

    resolved = CredentialProvider(keyring).resolve(
        "wallet", CredentialKind.VIVO_API, interactive=False
    )

    assert isinstance(resolved, VivoApiCredential)
    assert resolved.access_key.get_secret_value() == "environment-access"
    assert resolved.secret_key.get_secret_value() == "environment-secret"


def test_vivo_environment_file_precedes_keyring(tmp_path: Path) -> None:
    source = tmp_path / "vivo.json"
    source.write_text(
        json.dumps(vivo_value(access_key="file-access", secret_key="file-secret")),
        encoding="utf-8",
    )
    keyring = MemoryKeyring()
    keyring.set(
        "wallet",
        VivoApiCredential.model_validate(
            vivo_value(access_key="keyring-access", secret_key="keyring-secret")
        ).to_storage_json(),
        CredentialKind.VIVO_API,
    )

    resolved = CredentialProvider(
        keyring,
        environment={"STOREHELPER_VIVO_CREDENTIALS_FILE": str(source)},
    ).resolve("wallet", CredentialKind.VIVO_API, interactive=False)

    assert isinstance(resolved, VivoApiCredential)
    assert resolved.access_key.get_secret_value() == "file-access"


@pytest.mark.parametrize(
    "environment, expected_code",
    [
        ({"STOREHELPER_VIVO_ACCESS_KEY": "only-access"}, "CREDENTIAL_ENV_INCOMPLETE"),
        ({"STOREHELPER_VIVO_SECRET_KEY": "only-secret"}, "CREDENTIAL_ENV_INCOMPLETE"),
        (
            {
                "STOREHELPER_VIVO_CREDENTIALS_FILE": "/private/not-read.json",
                "STOREHELPER_VIVO_ACCESS_KEY": "duplicate-access",
            },
            "CREDENTIAL_SOURCE_CONFLICT",
        ),
    ],
)
def test_vivo_environment_rejects_partial_or_conflicting_sources(
    environment: dict[str, str], expected_code: str
) -> None:
    provider = CredentialProvider(MemoryKeyring(), environment=environment)

    with pytest.raises(CredentialError) as raised:
        provider.resolve("wallet", CredentialKind.VIVO_API, interactive=False)

    assert raised.value.code == expected_code
    assert "only-secret" not in str(raised.value)
    assert "duplicate-access" not in str(raised.value)


def test_vivo_interactive_prompt_masks_both_values(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["prompt-access", "prompt-secret"])
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(answers))

    credential = SecurePrompt().prompt(CredentialKind.VIVO_API)

    assert isinstance(credential, VivoApiCredential)
    assert credential.access_key.get_secret_value() == "prompt-access"
    assert credential.secret_key.get_secret_value() == "prompt-secret"
