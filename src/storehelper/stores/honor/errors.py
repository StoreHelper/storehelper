"""Stable, secret-safe HONOR publishing errors."""

from __future__ import annotations

from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.errors import StoreVendorError


class HonorVendorError(StoreVendorError):
    def __init__(
        self,
        code: str,
        message: str,
        exit_code: ExitCode,
        *,
        vendor_code: str | None = None,
        status_code: int | None = None,
        resumable: bool = False,
    ) -> None:
        super().__init__(
            code,
            message,
            exit_code,
            vendor_code=vendor_code,
            resumable=resumable,
        )
        self.status_code = status_code
