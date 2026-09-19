"""Runtime construction must reuse the artifact-bound target unchanged."""

from __future__ import annotations

from typing import cast

from storehelper.config.models import ApplicationConfig
from storehelper.runtime import resolve_runtime
from storehelper.stores.base import StoreAdapter
from storehelper.stores.models import StoreName, StoreTarget


def test_resolve_runtime_accepts_prebound_target_for_minimal_config() -> None:
    app = ApplicationConfig.model_validate(
        {"stores": {"huawei": {"app_id": "123", "credential_profile": "release"}}}
    )
    target = StoreTarget(
        store=StoreName.HUAWEI,
        label="Huawei AppGallery (Android)",
        app_id="123",
        package_name="com.example.demo",
        credential_profile="release",
        language="zh-CN",
    )
    runtime = resolve_runtime(app, StoreName.HUAWEI, cast(StoreAdapter, object()), target=target)
    assert runtime.target is target
