"""Immutable registry of audited built-in publishing adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from storehelper.artifacts.models import ArtifactInfo
from storehelper.credentials.models import (
    AppleApiKey,
    CredentialError,
    GoogleServiceAccount,
    HuaweiServiceAccount,
    OppoApiCredential,
    StoreCredential,
    XiaomiApiCredential,
)
from storehelper.stores.apple.adapter import AppleAdapter
from storehelper.stores.apple.auth import AppleAuth
from storehelper.stores.apple.client import AppleClient
from storehelper.stores.apple.package import validate_ipa
from storehelper.stores.base import StoreAdapter
from storehelper.stores.google_play.adapter import GooglePlayAdapter
from storehelper.stores.google_play.auth import GoogleAuth
from storehelper.stores.google_play.client import GooglePlayClient
from storehelper.stores.google_play.package import validate_google_play_artifact
from storehelper.stores.harmonyos.adapter import HarmonyOSAdapter
from storehelper.stores.harmonyos.client import HarmonyOSClient
from storehelper.stores.harmonyos.package import validate_harmonyos_artifact
from storehelper.stores.huawei.adapter import HuaweiAndroidAdapter
from storehelper.stores.huawei.auth import HuaweiAuth
from storehelper.stores.huawei.client import HuaweiClient
from storehelper.stores.huawei.package import validate_package
from storehelper.stores.models import (
    CredentialKind,
    StoreCapabilities,
    StoreName,
    StoreTarget,
)
from storehelper.stores.oppo.adapter import OppoAdapter
from storehelper.stores.oppo.auth import OppoAuth
from storehelper.stores.oppo.client import OppoClient
from storehelper.stores.oppo.package import validate_oppo_artifact
from storehelper.stores.vivo.package import validate_vivo_artifact
from storehelper.stores.xiaomi.adapter import XiaomiAdapter
from storehelper.stores.xiaomi.auth import XiaomiAuth
from storehelper.stores.xiaomi.client import XiaomiClient
from storehelper.stores.xiaomi.package import validate_xiaomi_artifact, validate_xiaomi_target

ArtifactValidator = Callable[[Path], ArtifactInfo]
AdapterFactory = Callable[[StoreCredential, httpx.AsyncClient], StoreAdapter]
TargetValidator = Callable[[StoreTarget], None]


@dataclass(frozen=True)
class AdapterRegistration:
    store: StoreName
    label: str
    capabilities: StoreCapabilities
    validator: ArtifactValidator
    factory: AdapterFactory | None
    target_validator: TargetValidator | None = None


def _huawei_factory(
    account: StoreCredential,
    http: httpx.AsyncClient,
) -> StoreAdapter:
    if not isinstance(account, HuaweiServiceAccount):
        raise CredentialError(
            "CREDENTIAL_KIND_MISMATCH",
            "Huawei publishing requires a Huawei Service Account profile.",
        )
    return HuaweiAndroidAdapter(HuaweiClient(auth=HuaweiAuth(account), http=http))


def _harmonyos_factory(
    account: StoreCredential,
    http: httpx.AsyncClient,
) -> StoreAdapter:
    if not isinstance(account, HuaweiServiceAccount):
        raise CredentialError(
            "CREDENTIAL_KIND_MISMATCH",
            "HarmonyOS publishing requires a Huawei Service Account profile.",
        )
    return HarmonyOSAdapter(HarmonyOSClient(auth=HuaweiAuth(account), http=http))


def _apple_factory(
    account: StoreCredential,
    http: httpx.AsyncClient,
) -> StoreAdapter:
    if not isinstance(account, AppleApiKey):
        raise CredentialError(
            "CREDENTIAL_KIND_MISMATCH",
            "Apple publishing requires an Apple API key profile.",
        )
    return AppleAdapter(AppleClient(auth=AppleAuth(account), http=http))


def _google_factory(
    account: StoreCredential,
    http: httpx.AsyncClient,
) -> StoreAdapter:
    if not isinstance(account, GoogleServiceAccount):
        raise CredentialError(
            "CREDENTIAL_KIND_MISMATCH",
            "Google Play publishing requires a Google service account profile.",
        )
    auth = GoogleAuth(account, http)
    return GooglePlayAdapter(GooglePlayClient(auth=auth, http=http))


def _xiaomi_factory(
    account: StoreCredential,
    http: httpx.AsyncClient,
) -> StoreAdapter:
    if not isinstance(account, XiaomiApiCredential):
        raise CredentialError(
            "CREDENTIAL_KIND_MISMATCH",
            "Xiaomi publishing requires a Xiaomi API credential profile.",
        )
    return XiaomiAdapter(
        XiaomiClient(
            auth=XiaomiAuth(account),
            credential=account,
            http=http,
        )
    )


def _oppo_factory(
    account: StoreCredential,
    http: httpx.AsyncClient,
) -> StoreAdapter:
    if not isinstance(account, OppoApiCredential):
        raise CredentialError(
            "CREDENTIAL_KIND_MISMATCH",
            "OPPO publishing requires an OPPO API credential profile.",
        )
    return OppoAdapter(
        OppoClient(
            credential=account,
            auth=OppoAuth(account),
            http=http,
        )
    )


_REGISTRATIONS = {
    StoreName.HUAWEI: AdapterRegistration(
        store=StoreName.HUAWEI,
        label="Huawei AppGallery (Android)",
        capabilities=StoreCapabilities(
            credential_kind=CredentialKind.HUAWEI_SERVICE_ACCOUNT,
            artifact_suffixes=(".apk", ".aab"),
            requires_processing_poll=True,
            requires_release_notes=True,
            supports_review_status=True,
        ),
        validator=validate_package,
        factory=_huawei_factory,
    ),
    StoreName.HARMONYOS: AdapterRegistration(
        store=StoreName.HARMONYOS,
        label="Huawei AppGallery (HarmonyOS)",
        capabilities=StoreCapabilities(
            credential_kind=CredentialKind.HUAWEI_SERVICE_ACCOUNT,
            artifact_suffixes=(".app", ".hap"),
            requires_processing_poll=True,
            requires_release_notes=True,
            supports_review_status=True,
        ),
        validator=validate_harmonyos_artifact,
        factory=_harmonyos_factory,
    ),
    StoreName.APPLE: AdapterRegistration(
        store=StoreName.APPLE,
        label="Apple App Store",
        capabilities=StoreCapabilities(
            credential_kind=CredentialKind.APPLE_API_KEY,
            artifact_suffixes=(".ipa",),
            requires_processing_poll=True,
            requires_release_notes=False,
            supports_review_status=True,
        ),
        validator=validate_ipa,
        factory=_apple_factory,
    ),
    StoreName.GOOGLE_PLAY: AdapterRegistration(
        store=StoreName.GOOGLE_PLAY,
        label="Google Play",
        capabilities=StoreCapabilities(
            credential_kind=CredentialKind.GOOGLE_SERVICE_ACCOUNT,
            artifact_suffixes=(".apk", ".aab"),
            requires_processing_poll=False,
            requires_release_notes=False,
            supports_review_status=True,
        ),
        validator=validate_google_play_artifact,
        factory=_google_factory,
    ),
    StoreName.XIAOMI: AdapterRegistration(
        store=StoreName.XIAOMI,
        label="Xiaomi App Store",
        capabilities=StoreCapabilities(
            credential_kind=CredentialKind.XIAOMI_API,
            artifact_suffixes=(".apk",),
            requires_processing_poll=False,
            requires_release_notes=True,
            supports_review_status=False,
            atomic_submission=True,
            supports_no_submit=False,
        ),
        validator=validate_xiaomi_artifact,
        factory=_xiaomi_factory,
        target_validator=validate_xiaomi_target,
    ),
    StoreName.OPPO: AdapterRegistration(
        store=StoreName.OPPO,
        label="OPPO Software Store",
        capabilities=StoreCapabilities(
            credential_kind=CredentialKind.OPPO_API,
            artifact_suffixes=(".apk",),
            requires_processing_poll=False,
            requires_release_notes=True,
            supports_review_status=True,
            atomic_submission=True,
            staged_submission=True,
            supports_no_submit=False,
        ),
        validator=validate_oppo_artifact,
        factory=_oppo_factory,
    ),
    StoreName.VIVO: AdapterRegistration(
        store=StoreName.VIVO,
        label="vivo App Store",
        capabilities=StoreCapabilities(
            credential_kind=CredentialKind.VIVO_API,
            artifact_suffixes=(".apk",),
            requires_processing_poll=False,
            requires_release_notes=True,
            supports_review_status=True,
            atomic_submission=True,
            staged_submission=True,
            supports_no_submit=False,
        ),
        validator=validate_vivo_artifact,
        factory=None,
    ),
}


def registered_store_names() -> tuple[StoreName, ...]:
    return tuple(store for store, registration in _REGISTRATIONS.items() if registration.factory)


def get_registration(store: StoreName) -> AdapterRegistration:
    return _REGISTRATIONS[store]
