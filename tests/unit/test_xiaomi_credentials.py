from __future__ import annotations

import json
from pathlib import Path

import pytest

from storehelper.credentials.models import CredentialError, XiaomiApiCredential
from storehelper.credentials.providers import CredentialProvider, MemoryKeyring, SecurePrompt
from storehelper.credentials.service import CredentialService
from storehelper.stores.models import CredentialKind


def xiaomi_value(certificate: str, **updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "username": "developer@example.com",
        "api_secret": "xiaomi-api-private-value",
        "public_key_certificate": certificate,
        "test_accounts": {
            "zh_CN": {
                "accounts": [
                    {
                        "login_type": 1,
                        "account": "reviewer@example.com",
                        "password": "review-password",
                        "access_code": "invite-code",
                    },
                    {
                        "login_type": 2,
                        "account": "13800138000",
                        "password": "123456",
                    },
                ],
                "audit_notes": "Use the first account for the main flow.",
            }
        },
    }
    value.update(updates)
    return value


def test_xiaomi_credential_validates_certificate_and_converts_structured_accounts(
    rsa_public_certificate: str,
) -> None:
    credential = XiaomiApiCredential.model_validate(xiaomi_value(rsa_public_certificate))

    assert credential.username == "developer@example.com"
    assert credential.review_accounts_api_value() == {
        "zh_CN": {
            "accounts": [
                {
                    "t": 1,
                    "a": "reviewer@example.com",
                    "p": "review-password",
                    "c": "invite-code",
                },
                {"t": 2, "a": "13800138000", "p": "123456"},
            ],
            "auditNotes": "Use the first account for the main flow.",
        }
    }
    stored = json.loads(credential.to_storage_json())
    assert stored["credential_kind"] == "xiaomi_api"
    assert stored["credential"]["test_accounts"]["zh_CN"]["accounts"][0]["password"]


@pytest.mark.parametrize(
    "updates",
    [
        {"username": "not-an-email"},
        {"api_secret": " "},
        {"public_key_certificate": "not-a-certificate"},
        {"unexpected": "value"},
    ],
)
def test_xiaomi_credential_rejects_invalid_base_fields_without_echoing_values(
    rsa_public_certificate: str,
    updates: dict[str, object],
) -> None:
    with pytest.raises(CredentialError) as raised:
        XiaomiApiCredential.model_validate(xiaomi_value(rsa_public_certificate, **updates))

    assert "xiaomi-api-private-value" not in str(raised.value)
    assert "not-a-certificate" not in str(raised.value)


def test_xiaomi_credential_requires_an_rsa_x509_certificate(
    rsa_public_certificate: str,
    p256_public_certificate: str,
) -> None:
    with pytest.raises(CredentialError) as raised:
        XiaomiApiCredential.model_validate(
            xiaomi_value(
                rsa_public_certificate,
                public_key_certificate=p256_public_certificate,
            )
        )

    assert "RSA public key" in raised.value.message
    assert p256_public_certificate not in str(raised.value)


@pytest.mark.parametrize(
    "test_accounts",
    [
        {"zh-CN": {"audit_notes": "bad locale"}},
        {"zh_CN": {"accounts": [{"login_type": 1, "account": "only-one"}]}},
        {"zh_CN": {"accounts": [{"login_type": 3, "access_code": "invite"}]}},
        {
            "zh_CN": {
                "accounts": [
                    {"login_type": 1, "account": f"account-{index}", "password": "p"}
                    for index in range(6)
                ]
            }
        },
        {"zh_CN": {"accounts": [], "audit_notes": "a" * 501}},
        {"zh_CN": {"accounts": [{"login_type": 1, "account": "a" * 51, "password": "password"}]}},
        {"zh_CN": {}},
    ],
)
def test_xiaomi_structured_review_accounts_enforce_vendor_limits(
    rsa_public_certificate: str,
    test_accounts: dict[str, object],
) -> None:
    with pytest.raises(CredentialError):
        XiaomiApiCredential.model_validate(
            xiaomi_value(rsa_public_certificate, test_accounts=test_accounts)
        )


def test_xiaomi_nested_secrets_are_masked_by_repr_and_dump(
    rsa_public_certificate: str,
) -> None:
    credential = XiaomiApiCredential.model_validate(xiaomi_value(rsa_public_certificate))
    rendered = repr(credential) + str(credential.model_dump())

    assert "xiaomi-api-private-value" not in rendered
    assert "review-password" not in rendered
    assert "invite-code" not in rendered
    assert rsa_public_certificate not in rendered


@pytest.mark.usefixtures("clean_xiaomi_env")
def test_xiaomi_environment_values_override_keyring(
    monkeypatch: pytest.MonkeyPatch,
    rsa_public_certificate: str,
) -> None:
    keyring = MemoryKeyring()
    stored = XiaomiApiCredential.model_validate(
        xiaomi_value(rsa_public_certificate, username="keyring@example.com")
    )
    keyring.set("release", stored.to_storage_json(), CredentialKind.XIAOMI_API)
    monkeypatch.setenv("STOREHELPER_XIAOMI_USERNAME", "env@example.com")
    monkeypatch.setenv("STOREHELPER_XIAOMI_API_SECRET", "env-secret")
    monkeypatch.setenv("STOREHELPER_XIAOMI_PUBLIC_KEY_CERTIFICATE", rsa_public_certificate)
    monkeypatch.setenv(
        "STOREHELPER_XIAOMI_TEST_ACCOUNTS_JSON",
        json.dumps(xiaomi_value(rsa_public_certificate)["test_accounts"]),
    )

    resolved = CredentialProvider(keyring).resolve(
        "release", CredentialKind.XIAOMI_API, interactive=False
    )

    assert isinstance(resolved, XiaomiApiCredential)
    assert resolved.username == "env@example.com"
    assert resolved.review_accounts_api_value() is not None


def test_xiaomi_credential_file_conflicts_with_individual_values(
    tmp_path: Path,
    rsa_public_certificate: str,
) -> None:
    path = tmp_path / "xiaomi.json"
    path.write_text(json.dumps(xiaomi_value(rsa_public_certificate)), encoding="utf-8")
    provider = CredentialProvider(
        MemoryKeyring(),
        environment={
            "STOREHELPER_XIAOMI_CREDENTIALS_FILE": str(path),
            "STOREHELPER_XIAOMI_USERNAME": "duplicate@example.com",
        },
    )

    with pytest.raises(CredentialError) as raised:
        provider.resolve("release", CredentialKind.XIAOMI_API, interactive=False)

    assert raised.value.code == "CREDENTIAL_SOURCE_CONFLICT"


def test_xiaomi_environment_credential_file_is_loaded(
    tmp_path: Path,
    rsa_public_certificate: str,
) -> None:
    path = tmp_path / "xiaomi.json"
    path.write_text(json.dumps(xiaomi_value(rsa_public_certificate)), encoding="utf-8")
    provider = CredentialProvider(
        MemoryKeyring(),
        environment={"STOREHELPER_XIAOMI_CREDENTIALS_FILE": str(path)},
    )

    resolved = provider.resolve("ignored", CredentialKind.XIAOMI_API, interactive=False)

    assert isinstance(resolved, XiaomiApiCredential)
    assert resolved.username == "developer@example.com"


def test_xiaomi_environment_rejects_incomplete_or_invalid_review_json(
    rsa_public_certificate: str,
) -> None:
    incomplete = CredentialProvider(
        MemoryKeyring(),
        environment={"STOREHELPER_XIAOMI_USERNAME": "developer@example.com"},
    )
    invalid_json = CredentialProvider(
        MemoryKeyring(),
        environment={
            "STOREHELPER_XIAOMI_USERNAME": "developer@example.com",
            "STOREHELPER_XIAOMI_API_SECRET": "secret",
            "STOREHELPER_XIAOMI_PUBLIC_KEY_CERTIFICATE": rsa_public_certificate,
            "STOREHELPER_XIAOMI_TEST_ACCOUNTS_JSON": "not-json",
        },
    )

    with pytest.raises(CredentialError) as missing:
        incomplete.resolve("release", CredentialKind.XIAOMI_API, interactive=False)
    with pytest.raises(CredentialError) as malformed:
        invalid_json.resolve("release", CredentialKind.XIAOMI_API, interactive=False)

    assert missing.value.code == "CREDENTIAL_ENV_INCOMPLETE"
    assert malformed.value.code == "CREDENTIAL_ENV_INVALID"
    assert "not-json" not in str(malformed.value)


def test_xiaomi_file_keyring_and_namespace_resolution(
    tmp_path: Path,
    rsa_public_certificate: str,
) -> None:
    source = tmp_path / "xiaomi.json"
    source.write_text(json.dumps(xiaomi_value(rsa_public_certificate)), encoding="utf-8")
    keyring = MemoryKeyring()
    service = CredentialService(keyring)

    service.import_file("release", source, CredentialKind.XIAOMI_API)
    resolved = CredentialProvider(keyring, environment={}).resolve(
        "release", CredentialKind.XIAOMI_API, interactive=False
    )

    assert isinstance(resolved, XiaomiApiCredential)
    assert service.list_profiles(CredentialKind.XIAOMI_API) == ["release"]
    assert service.list_profiles(CredentialKind.HUAWEI_SERVICE_ACCOUNT) == []


def test_xiaomi_interactive_prompt_collects_only_base_secrets(
    monkeypatch: pytest.MonkeyPatch,
    rsa_public_certificate: str,
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "developer@example.com")
    answers = iter(["api-secret", rsa_public_certificate])
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(answers))

    credential = SecurePrompt().prompt(CredentialKind.XIAOMI_API)

    assert isinstance(credential, XiaomiApiCredential)
    assert credential.test_accounts is None
