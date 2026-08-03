"""Huawei response envelope parsing and safe error translation."""

from __future__ import annotations

import json
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from storehelper.domain.errors import redact
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.errors import ArtifactStillProcessingError, StoreVendorError

_MESSAGES = {
    "204144641": "Huawei rejected an invalid parameter.",
    "204144645": "Huawei could not create an upload authorization code.",
    "204144658": "Huawei FileServer upload failed.",
    "204144662": (
        "Huawei could not add the package; verify the logical file name and package limit."
    ),
    "204144664": "Huawei pre-submit validation found missing application information.",
    "204144665": "Huawei rejected the Service Account authorization.",
    "204144666": "The Huawei developer account is suspended or not verified.",
    "204144717": "Huawei could not read the package detection result.",
    "204144718": "Huawei could not read the package compilation state.",
    "204144722": "Huawei detected inconsistent signatures between APKs.",
    "204144723": "The package signature differs from an existing release.",
    "204144727": "Huawei is still compiling the package.",
    "204144735": "Huawei is still performing package security detection.",
}
_AUTH_CODES = {"1101", "204144665", "204144666"}
_COMPILING_CODES = {"204144727", "204144735"}


class HuaweiResponseStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str = ""
    hint: str = ""

    @property
    def success(self) -> bool:
        return self.code == "0"


class HuaweiVendorError(StoreVendorError):
    pass


class HuaweiArtifactStillProcessingError(
    ArtifactStillProcessingError,
    HuaweiVendorError,
):
    """Compatibility error carrying both generic and Huawei classifications."""


def _protocol_error(message: str) -> HuaweiVendorError:
    return HuaweiVendorError(
        "HUAWEI_RESPONSE_INVALID",
        message,
        ExitCode.VENDOR_REJECTION,
    )


def parse_huawei_response(data: Mapping[str, object]) -> HuaweiResponseStatus:
    """Parse Huawei's `ret` field whether returned as an object or JSON string."""

    raw = data.get("ret")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raise _protocol_error("Huawei response contains an invalid ret value.") from None
    if not isinstance(raw, Mapping) or "code" not in raw:
        raise _protocol_error("Huawei response does not contain a valid ret object.")
    code = str(raw.get("code"))
    message = redact(str(raw.get("msg") or ""))[:500]
    hint = redact(str(raw.get("hint") or ""))[:500]
    return HuaweiResponseStatus(code=code, message=message, hint=hint)


def translate_huawei_error(code: str, message: str | None = None) -> StoreVendorError:
    """Translate one Huawei code without exposing raw vendor payloads."""

    normalized = str(code)
    safe_vendor_message = redact(message or "")[:500]
    description = _MESSAGES.get(normalized, "Huawei rejected the request.")
    if safe_vendor_message and safe_vendor_message.lower() not in description.lower():
        description = f"{description} Huawei message: {safe_vendor_message}"
    if normalized in _COMPILING_CODES:
        return HuaweiArtifactStillProcessingError(
            "HUAWEI_PACKAGE_COMPILING",
            description,
            vendor_code=normalized,
        )
    if normalized in _AUTH_CODES:
        return HuaweiVendorError(
            "HUAWEI_AUTH_FAILED",
            description,
            ExitCode.AUTHENTICATION,
            vendor_code=normalized,
        )
    return HuaweiVendorError(
        "HUAWEI_REJECTED",
        description,
        ExitCode.VENDOR_REJECTION,
        vendor_code=normalized,
    )
