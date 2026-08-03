from __future__ import annotations

import asyncio

import httpx
import pytest

from storehelper.credentials.models import (
    AppleApiKey,
    CredentialError,
    GoogleServiceAccount,
    HuaweiServiceAccount,
    XiaomiApiCredential,
)
from storehelper.stores.apple.adapter import AppleAdapter
from storehelper.stores.google_play.adapter import GooglePlayAdapter
from storehelper.stores.harmonyos.adapter import HarmonyOSAdapter
from storehelper.stores.huawei.adapter import HuaweiAndroidAdapter
from storehelper.stores.models import CredentialKind, StoreName
from storehelper.stores.registry import get_registration, registered_store_names
from storehelper.stores.xiaomi.adapter import XiaomiAdapter


def test_registry_exposes_only_audited_builtin_stores() -> None:
    assert registered_store_names() == (
        StoreName.HUAWEI,
        StoreName.HARMONYOS,
        StoreName.APPLE,
        StoreName.GOOGLE_PLAY,
        StoreName.XIAOMI,
    )


def test_huawei_and_harmonyos_share_credentials_but_not_artifact_rules() -> None:
    huawei = get_registration(StoreName.HUAWEI)
    harmonyos = get_registration(StoreName.HARMONYOS)

    assert huawei.capabilities.credential_kind is CredentialKind.HUAWEI_SERVICE_ACCOUNT
    assert harmonyos.capabilities.credential_kind is CredentialKind.HUAWEI_SERVICE_ACCOUNT
    assert huawei.capabilities.artifact_suffixes == (".apk", ".aab")
    assert harmonyos.capabilities.artifact_suffixes == (".app", ".hap")
    assert huawei.validator is not harmonyos.validator


def test_apple_registration_declares_native_ios_capabilities() -> None:
    apple = get_registration(StoreName.APPLE)

    assert apple.label == "Apple App Store"
    assert apple.capabilities.credential_kind is CredentialKind.APPLE_API_KEY
    assert apple.capabilities.artifact_suffixes == (".ipa",)
    assert apple.capabilities.requires_processing_poll is True
    assert apple.capabilities.requires_release_notes is False
    assert apple.capabilities.supports_review_status is True


def test_google_registration_declares_audited_android_capabilities() -> None:
    google = get_registration(StoreName.GOOGLE_PLAY)

    assert google.label == "Google Play"
    assert google.capabilities.credential_kind is CredentialKind.GOOGLE_SERVICE_ACCOUNT
    assert google.capabilities.artifact_suffixes == (".apk", ".aab")
    assert google.capabilities.requires_release_notes is False
    assert google.capabilities.requires_processing_poll is False
    assert google.capabilities.supports_review_status is True
    assert google.factory is not None


def test_xiaomi_registration_declares_atomic_update_capabilities() -> None:
    xiaomi = get_registration(StoreName.XIAOMI)

    assert xiaomi.label == "Xiaomi App Store"
    assert xiaomi.capabilities.credential_kind is CredentialKind.XIAOMI_API
    assert xiaomi.capabilities.artifact_suffixes == (".apk",)
    assert xiaomi.capabilities.requires_release_notes is True
    assert xiaomi.capabilities.requires_processing_poll is False
    assert xiaomi.capabilities.supports_review_status is False
    assert xiaomi.capabilities.atomic_submission is True
    assert xiaomi.capabilities.supports_no_submit is False
    assert xiaomi.target_validator is not None
    assert xiaomi.factory is not None


def test_registry_builds_the_selected_adapter(
    rsa_private_key: str,
    p256_private_key: str,
    rsa_public_certificate: str,
) -> None:
    account = HuaweiServiceAccount(
        key_id="key-1",
        sub_account="sub-1",
        private_key=rsa_private_key,
    )
    apple_key = AppleApiKey(
        key_type="team",
        key_id="APPLEKEY1",
        issuer_id="issuer-1",
        private_key=p256_private_key,
    )
    google_key = GoogleServiceAccount(
        type="service_account",
        project_id="demo-project",
        private_key_id="google-key-1",
        private_key=rsa_private_key,
        client_email="storehelper@demo-project.iam.gserviceaccount.com",
    )
    xiaomi_key = XiaomiApiCredential(
        username="developer@example.com",
        api_secret="api-secret",
        public_key_certificate=rsa_public_certificate,
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))

    try:
        huawei = get_registration(StoreName.HUAWEI).factory(account, http)
        harmonyos = get_registration(StoreName.HARMONYOS).factory(account, http)
        apple_factory = get_registration(StoreName.APPLE).factory
        assert apple_factory is not None
        apple = apple_factory(apple_key, http)
        google_factory = get_registration(StoreName.GOOGLE_PLAY).factory
        assert google_factory is not None
        google = google_factory(google_key, http)
        xiaomi_factory = get_registration(StoreName.XIAOMI).factory
        assert xiaomi_factory is not None
        xiaomi = xiaomi_factory(xiaomi_key, http)
    finally:
        asyncio.run(http.aclose())

    assert isinstance(huawei, HuaweiAndroidAdapter)
    assert isinstance(harmonyos, HarmonyOSAdapter)
    assert isinstance(apple, AppleAdapter)
    assert isinstance(google, GooglePlayAdapter)
    assert isinstance(xiaomi, XiaomiAdapter)


def test_registry_rejects_wrong_credential_kind_for_apple(
    rsa_private_key: str,
) -> None:
    account = HuaweiServiceAccount(
        key_id="key-1",
        sub_account="sub-1",
        private_key=rsa_private_key,
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))

    try:
        factory = get_registration(StoreName.APPLE).factory
        assert factory is not None
        with pytest.raises(CredentialError, match="Apple API key"):
            factory(account, http)
    finally:
        asyncio.run(http.aclose())


def test_registry_rejects_wrong_credential_kind_for_google(
    p256_private_key: str,
) -> None:
    wrong = AppleApiKey(
        key_type="team",
        key_id="APPLEKEY1",
        issuer_id="issuer-1",
        private_key=p256_private_key,
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))

    try:
        factory = get_registration(StoreName.GOOGLE_PLAY).factory
        assert factory is not None
        with pytest.raises(CredentialError, match="Google service account"):
            factory(wrong, http)
    finally:
        asyncio.run(http.aclose())


def test_registry_rejects_wrong_credential_kind_for_xiaomi(
    rsa_private_key: str,
) -> None:
    wrong = HuaweiServiceAccount(
        key_id="key-1",
        sub_account="sub-1",
        private_key=rsa_private_key,
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))

    try:
        factory = get_registration(StoreName.XIAOMI).factory
        assert factory is not None
        with pytest.raises(CredentialError, match="Xiaomi API credential"):
            factory(wrong, http)
    finally:
        asyncio.run(http.aclose())
