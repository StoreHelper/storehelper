"""Immutable registry of audited built-in publishing adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from storehelper.artifacts.models import ArtifactInfo
from storehelper.credentials.models import HuaweiServiceAccount
from storehelper.stores.base import StoreAdapter
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
)

ArtifactValidator = Callable[[Path], ArtifactInfo]
AdapterFactory = Callable[[HuaweiServiceAccount, httpx.AsyncClient], StoreAdapter]


@dataclass(frozen=True)
class AdapterRegistration:
    store: StoreName
    label: str
    capabilities: StoreCapabilities
    validator: ArtifactValidator
    factory: AdapterFactory


def _huawei_factory(
    account: HuaweiServiceAccount,
    http: httpx.AsyncClient,
) -> StoreAdapter:
    return HuaweiAndroidAdapter(HuaweiClient(auth=HuaweiAuth(account), http=http))


def _harmonyos_factory(
    account: HuaweiServiceAccount,
    http: httpx.AsyncClient,
) -> StoreAdapter:
    return HarmonyOSAdapter(HarmonyOSClient(auth=HuaweiAuth(account), http=http))


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
}


def registered_store_names() -> tuple[StoreName, ...]:
    return tuple(_REGISTRATIONS)


def get_registration(store: StoreName) -> AdapterRegistration:
    return _REGISTRATIONS[store]
