"""Store adapter protocol used by the publishing application service."""

from __future__ import annotations

from typing import Protocol

from storehelper.stores.huawei.adapter import CompileStatus, ReviewStatus
from storehelper.stores.huawei.models import BoundPackage, HuaweiApp
from storehelper.stores.huawei.package import PackageInfo


class StoreAdapter(Protocol):
    async def verify(self, *, app_id: str, package_name: str) -> HuaweiApp: ...

    async def upload(self, *, app_id: str, package: PackageInfo) -> BoundPackage: ...

    async def compile_status(self, *, app_id: str, pkg_version: str) -> CompileStatus: ...

    async def update_release_notes(
        self,
        *,
        app_id: str,
        language: str,
        release_notes: str,
    ) -> None: ...

    async def submit(self, *, app_id: str) -> str: ...

    async def review_status(self, *, app_id: str) -> ReviewStatus: ...
