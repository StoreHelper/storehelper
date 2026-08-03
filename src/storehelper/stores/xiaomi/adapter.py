"""Xiaomi store-neutral adapter boundary."""

from __future__ import annotations

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.models import StoreName, StoreTarget, VerifiedApplication
from storehelper.stores.xiaomi.client import XiaomiClient
from storehelper.stores.xiaomi.errors import XiaomiVendorError


class XiaomiAdapter:
    def __init__(self, client: XiaomiClient) -> None:
        self._client = client

    @staticmethod
    def _validate_target(target: StoreTarget) -> str:
        if (
            target.store is not StoreName.XIAOMI
            or target.app_id != target.package_name
            or not target.package_name.strip()
        ):
            raise XiaomiVendorError(
                "XIAOMI_TARGET_INVALID",
                "Xiaomi publishing requires one matching existing package identity.",
                ExitCode.VENDOR_REJECTION,
            )
        return target.package_name

    async def verify(self, *, target: StoreTarget) -> VerifiedApplication:
        package_name = self._validate_target(target)
        return await self._client.query_package(package_name=package_name)
