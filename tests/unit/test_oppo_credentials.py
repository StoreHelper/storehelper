from __future__ import annotations

import json
from pathlib import Path

import pytest

from storehelper.credentials.models import CredentialError, OppoApiCredential
from storehelper.credentials.providers import CredentialProvider, MemoryKeyring, SecurePrompt
from storehelper.credentials.service import CredentialService
from storehelper.stores.models import CredentialKind


def oppo_value(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "client_id": "oppo-client-private-value",
        "client_secret": "oppo-secret-private-value",
    }
    value.update(updates)
    return value


def test_oppo_credential_is_bounded_secret_safe_and_serializable() -> None:
    credential = OppoApiCredential.model_validate(oppo_value())

    rendered = repr(credential) + str(credential) + str(credential.model_dump())
    stored = json.loads(credential.to_storage_json())

    assert "oppo-client-private-value" not in rendered
    assert "oppo-secret-private-value" not in rendered
    assert stored == {
        "credential_kind": "oppo_api",
        "credential": oppo_value(),
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"client_id": ""},
        {"client_id": " "},
        {"client_secret": ""},
        {"client_secret": " "},
        {"client_id": "i" * 513},
        {"client_secret": "s" * 513},
        {"unexpected": "sensitive-unsupported-value"},
    ],
)
def test_oppo_credential_rejects_invalid_values_without_echoing_them(
    updates: dict[str, object],
) -> None:
    value = oppo_value(**updates)

    with pytest.raises(CredentialError) as raised:
        OppoApiCredential.model_validate(value)

    rendered = str(raised.value)
    assert raised.value.code == "CREDENTIAL_INVALID"
    assert "oppo-client-private-value" not in rendered
    assert "oppo-secret-private-value" not in rendered
    assert "sensitive-unsupported-value" not in rendered


def test_oppo_friendly_credential_file_and_keyring_namespace(tmp_path: Path) -> None:
    source = tmp_path / "oppo.json"
    source.write_text(json.dumps(oppo_value()), encoding="utf-8")
    keyring = MemoryKeyring()
    service = CredentialService(keyring)

    service.import_file("wallet", source, CredentialKind.OPPO_API)
    resolved = CredentialProvider(keyring, environment={}).resolve(
        "wallet", CredentialKind.OPPO_API, interactive=False
    )

    assert isinstance(resolved, OppoApiCredential)
    assert resolved.client_id.get_secret_value() == "oppo-client-private-value"
    assert service.list_profiles(CredentialKind.OPPO_API) == ["wallet"]
    assert service.list_profiles(CredentialKind.HUAWEI_SERVICE_ACCOUNT) == []
    service.delete("wallet", CredentialKind.OPPO_API)
    assert service.list_profiles(CredentialKind.OPPO_API) == []


@pytest.mark.usefixtures("clean_oppo_env")
def test_oppo_complete_environment_pair_overrides_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keyring = MemoryKeyring()
    keyring.set(
        "wallet",
        OppoApiCredential.model_validate(
            oppo_value(client_id="keyring-client", client_secret="keyring-secret")
        ).to_storage_json(),
        CredentialKind.OPPO_API,
    )
    monkeypatch.setenv("STOREHELPER_OPPO_CLIENT_ID", "environment-client")
    monkeypatch.setenv("STOREHELPER_OPPO_CLIENT_SECRET", "environment-secret")

    resolved = CredentialProvider(keyring).resolve(
        "wallet", CredentialKind.OPPO_API, interactive=False
    )

    assert isinstance(resolved, OppoApiCredential)
    assert resolved.client_id.get_secret_value() == "environment-client"
    assert resolved.client_secret.get_secret_value() == "environment-secret"


def test_oppo_environment_file_precedes_keyring(tmp_path: Path) -> None:
    source = tmp_path / "oppo.json"
    source.write_text(
        json.dumps(oppo_value(client_id="file-client", client_secret="file-secret")),
        encoding="utf-8",
    )
    keyring = MemoryKeyring()
    keyring.set(
        "wallet",
        OppoApiCredential.model_validate(
            oppo_value(client_id="keyring-client", client_secret="keyring-secret")
        ).to_storage_json(),
        CredentialKind.OPPO_API,
    )

    resolved = CredentialProvider(
        keyring,
        environment={"STOREHELPER_OPPO_CREDENTIALS_FILE": str(source)},
    ).resolve("wallet", CredentialKind.OPPO_API, interactive=False)

    assert isinstance(resolved, OppoApiCredential)
    assert resolved.client_id.get_secret_value() == "file-client"


@pytest.mark.parametrize(
    "environment, expected_code",
    [
        ({"STOREHELPER_OPPO_CLIENT_ID": "only-client"}, "CREDENTIAL_ENV_INCOMPLETE"),
        ({"STOREHELPER_OPPO_CLIENT_SECRET": "only-secret"}, "CREDENTIAL_ENV_INCOMPLETE"),
        (
            {
                "STOREHELPER_OPPO_CREDENTIALS_FILE": "/private/not-read.json",
                "STOREHELPER_OPPO_CLIENT_ID": "duplicate-client",
            },
            "CREDENTIAL_SOURCE_CONFLICT",
        ),
    ],
)
def test_oppo_environment_rejects_partial_or_conflicting_sources(
    environment: dict[str, str],
    expected_code: str,
) -> None:
    provider = CredentialProvider(MemoryKeyring(), environment=environment)

    with pytest.raises(CredentialError) as raised:
        provider.resolve("wallet", CredentialKind.OPPO_API, interactive=False)

    assert raised.value.code == expected_code
    assert "only-secret" not in str(raised.value)
    assert "duplicate-client" not in str(raised.value)


def test_oppo_interactive_prompt_masks_both_values(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["prompt-client", "prompt-secret"])
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(answers))

    credential = SecurePrompt().prompt(CredentialKind.OPPO_API)

    assert isinstance(credential, OppoApiCredential)
    assert credential.client_id.get_secret_value() == "prompt-client"
    assert credential.client_secret.get_secret_value() == "prompt-secret"
