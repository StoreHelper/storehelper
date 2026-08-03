"""Store-neutral vendor errors used by publishing orchestration."""

from __future__ import annotations

from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode


class StoreVendorError(StoreHelperError):
    """Base class for safe errors returned by a publishing store."""


class ArtifactStillProcessingError(StoreVendorError):
    """The store accepted an artifact but is not ready for submission yet."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        vendor_code: str | None = None,
    ) -> None:
        super().__init__(
            code,
            message,
            ExitCode.RESUMABLE_TIMEOUT,
            resumable=True,
            vendor_code=vendor_code,
        )
