"""Typed errors and defensive secret redaction."""

from __future__ import annotations

import re

from storehelper.domain.exit_codes import ExitCode

_PEM_PATTERN = re.compile(
    r"-----BEGIN(?: RSA)? PRIVATE KEY-----.*?-----END(?: RSA)? PRIVATE KEY-----",
    re.DOTALL,
)
_AUTHORIZATION_PATTERN = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+")
_JWT_PATTERN = re.compile(r"\b[A-Za-z0-9_-]{3,}\.[A-Za-z0-9_-]{3,}\.[A-Za-z0-9_-]{3,}\b")
_SECRET_FIELD_PATTERN = re.compile(
    r"(?i)(\b(?:authCode|private_key|access_token|client_secret)\b\s*[=:]\s*)[^\s,;]+"
)


def redact(value: str) -> str:
    """Remove known secret shapes from user-visible text."""

    redacted = _PEM_PATTERN.sub("[REDACTED]", str(value))
    redacted = _AUTHORIZATION_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = _SECRET_FIELD_PATTERN.sub(r"\1[REDACTED]", redacted)
    return _JWT_PATTERN.sub("[REDACTED]", redacted)


class StoreHelperError(Exception):
    """Base error carrying a stable public code and exit status."""

    def __init__(
        self,
        code: str,
        message: str,
        exit_code: ExitCode,
        *,
        resumable: bool = False,
        vendor_code: str | None = None,
    ) -> None:
        self.code = code
        self.message = redact(message)
        self.exit_code = exit_code
        self.resumable = resumable
        self.vendor_code = vendor_code
        super().__init__(self.message)
