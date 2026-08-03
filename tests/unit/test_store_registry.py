from __future__ import annotations

import asyncio

import httpx
import pytest

from storehelper.credentials.models import (
    AppleApiKey,
    CredentialError,
    GoogleServiceAccount,
    HuaweiServiceAccount,
    OppoApiCredential,
    VivoApiCredential,
    XiaomiApiCredential,
)
from storehelper.stores.apple.adapter import AppleAdapter
from storehelper.stores.google_play.adapter import GooglePlayAdapter
from storehelper.stores.harmonyos.adapter import HarmonyOSAdapter
from storehelper.stores.huawei.adapter import HuaweiAndroidAdapter
from storehelper.stores.models import CredentialKind, StoreName
from storehelper.stores.oppo.adapter import OppoAdapter
from storehelper.stores.registry import get_registration, registered_store_names
from storehelper.stores.vivo.adapter import VivoAdapter
from storehelper.stores.xiaomi.adapter import XiaomiAdapter


def test_registry_exposes_only_audited_builtin_stores() -> None:
    assert registered_store_names() == (
        StoreName.HUAWEI,
        StoreName.HARMONYOS,
        StoreName.APPLE,
        StoreName.GOOGLE_PLAY,
        StoreName.XIAOMI,
        StoreName.OPPO,
        StoreName.VIVO,
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


def test_oppo_registration_declares_staged_update_capabilities() -> None:
    oppo = get_registration(StoreName.OPPO)

    assert oppo.label == "OPPO Software Store"
    assert oppo.capabilities.credential_kind is CredentialKind.OPPO_API
    assert oppo.capabilities.artifact_suffixes == (".apk",)
    assert oppo.capabilities.requires_release_notes is True
    assert oppo.capabilities.requires_processing_poll is False
    assert oppo.capabilities.supports_review_status is True
    assert oppo.capabilities.atomic_submission is True
    assert oppo.capabilities.staged_submission is True
    assert oppo.capabilities.supports_no_submit is False
    assert oppo.factory is not None


def test_vivo_registration_declares_staged_update_capabilities() -> None:
    vivo = get_registration(StoreName.VIVO)

    assert vivo.label == "vivo App Store"
    assert vivo.capabilities.credential_kind is CredentialKind.VIVO_API
    assert vivo.capabilities.artifact_suffixes == (".apk",)
    assert vivo.capabilities.requires_release_notes is True
    assert vivo.capabilities.requires_processing_poll is False
    assert vivo.capabilities.supports_review_status is True
    assert vivo.capabilities.atomic_submission is True
    assert vivo.capabilities.staged_submission is True
    assert vivo.capabilities.supports_no_submit is False
    assert vivo.factory is not None


def test_honor_registration_declares_resumable_update_capabilities() -> None:
    honor = get_registration(StoreName.HONOR)

    assert honor.label == "HONOR App Market"
    assert honor.capabilities.credential_kind is CredentialKind.HONOR_API
    assert honor.capabilities.artifact_suffixes == (".apk",)
    assert honor.capabilities.requires_release_notes is True
    assert honor.capabilities.requires_processing_poll is False
    assert honor.capabilities.supports_review_status is True
    assert honor.capabilities.atomic_submission is False
    assert honor.capabilities.supports_no_submit is False
    assert honor.factory is None


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
    oppo_key = OppoApiCredential(
        client_id="oppo-client",
        client_secret="oppo-secret",
    )
    vivo_key = VivoApiCredential(
        access_key="vivo-access",
        secret_key="vivo-secret",
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
        oppo_factory = get_registration(StoreName.OPPO).factory
        assert oppo_factory is not None
        oppo = oppo_factory(oppo_key, http)
        vivo_factory = get_registration(StoreName.VIVO).factory
        assert vivo_factory is not None
        vivo = vivo_factory(vivo_key, http)
    finally:
        asyncio.run(http.aclose())

    assert isinstance(huawei, HuaweiAndroidAdapter)
    assert isinstance(harmonyos, HarmonyOSAdapter)
    assert isinstance(apple, AppleAdapter)
    assert isinstance(google, GooglePlayAdapter)
    assert isinstance(xiaomi, XiaomiAdapter)
    assert isinstance(oppo, OppoAdapter)
    assert isinstance(vivo, VivoAdapter)


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


def test_registry_rejects_wrong_credential_kind_for_oppo(
    rsa_private_key: str,
) -> None:
    wrong = HuaweiServiceAccount(
        key_id="key-1",
        sub_account="sub-1",
        private_key=rsa_private_key,
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))

    try:
        factory = get_registration(StoreName.OPPO).factory
        assert factory is not None
        with pytest.raises(CredentialError, match="OPPO API credential"):
            factory(wrong, http)
    finally:
        asyncio.run(http.aclose())


def test_registry_rejects_wrong_credential_kind_for_vivo(
    rsa_private_key: str,
) -> None:
    wrong = HuaweiServiceAccount(
        key_id="key-1",
        sub_account="sub-1",
        private_key=rsa_private_key,
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))

    try:
        factory = get_registration(StoreName.VIVO).factory
        assert factory is not None
        with pytest.raises(CredentialError, match="vivo API credential"):
            factory(wrong, http)
    finally:
        asyncio.run(http.aclose())
