"""Construction of a selected store's publishing runtime."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from storehelper.config.loader import resolve_store_target
from storehelper.config.models import ApplicationConfig
from storehelper.credentials.models import StoreCredential
from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode
from storehelper.publishing.service import ArtifactValidator, TargetValidator
from storehelper.stores.base import StoreAdapter
from storehelper.stores.models import StoreCapabilities, StoreName, StoreTarget
from storehelper.stores.registry import get_registration


@dataclass(frozen=True)
class StoreRuntime:
    adapter: StoreAdapter
    target: StoreTarget
    validator: ArtifactValidator
    capabilities: StoreCapabilities
    target_validator: TargetValidator | None


def resolve_runtime(
    application: ApplicationConfig,
    store: StoreName,
    adapter: StoreAdapter,
) -> StoreRuntime:
    registration = get_registration(store)
    return StoreRuntime(
        adapter=adapter,
        target=resolve_store_target(application, store),
        validator=registration.validator,
        capabilities=registration.capabilities,
        target_validator=registration.target_validator,
    )


def build_runtime(
    application: ApplicationConfig,
    store: StoreName,
    account: StoreCredential,
    http: httpx.AsyncClient,
) -> StoreRuntime:
    registration = get_registration(store)
    if registration.factory is None:
        raise StoreHelperError(
            "STORE_ADAPTER_UNAVAILABLE",
            f"The {registration.label} adapter is not available.",
            ExitCode.USAGE,
        )
    return resolve_runtime(application, store, registration.factory(account, http))
