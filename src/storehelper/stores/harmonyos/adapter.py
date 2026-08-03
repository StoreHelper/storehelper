"""Typed HarmonyOS publishing operations."""

from __future__ import annotations

from storehelper.stores.harmonyos.client import HarmonyOSClient
from storehelper.stores.models import VerifiedApplication


class HarmonyOSAdapter:
    def __init__(self, client: HarmonyOSClient) -> None:
        self._client = client

    async def verify(
        self,
        *,
        app_id: str,
        package_name: str,
    ) -> VerifiedApplication:
        return await self._client.verify_app(app_id=app_id, package_name=package_name)
