"""Local Xiaomi APK and icon validation."""

from __future__ import annotations

import os
from pathlib import Path

from storehelper.artifacts.models import ArtifactInfo
from storehelper.stores.huawei.package import PackageError, validate_package
from storehelper.stores.models import StoreTarget

MAX_XIAOMI_PACKAGE_SIZE = 2 * 1024 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def validate_xiaomi_artifact(path: Path) -> ArtifactInfo:
    """Accept one valid APK within Xiaomi's documented upload limit."""

    if path.suffix.lower() != ".apk":
        raise PackageError(
            "PACKAGE_TYPE_UNSUPPORTED",
            f"Xiaomi publishing accepts only APK files: {path.name}",
        )
    try:
        size = path.stat().st_size
    except OSError:
        return validate_package(path)
    if size > MAX_XIAOMI_PACKAGE_SIZE:
        raise PackageError("PACKAGE_TOO_LARGE", "Xiaomi APKs may not exceed 2 GiB.")
    return validate_package(path)


def validate_xiaomi_target(target: StoreTarget) -> None:
    """Validate the public icon input before credentials or network are used."""

    icon = target.icon_path
    if icon is None:
        raise PackageError("XIAOMI_ICON_MISSING", "Xiaomi publishing requires an icon.")
    if icon.suffix.lower() != ".png":
        raise PackageError("XIAOMI_ICON_INVALID", "Xiaomi icon must be a PNG file.")
    if not icon.is_file() or not os.access(icon, os.R_OK):
        raise PackageError("XIAOMI_ICON_NOT_READABLE", f"Xiaomi icon is not readable: {icon}")
    try:
        with icon.open("rb") as image:
            header = image.read(24)
    except OSError:
        raise PackageError(
            "XIAOMI_ICON_NOT_READABLE", f"Xiaomi icon is not readable: {icon}"
        ) from None
    valid_header = (
        len(header) == 24
        and header[:8] == _PNG_SIGNATURE
        and header[8:12] == b"\x00\x00\x00\r"
        and header[12:16] == b"IHDR"
        and int.from_bytes(header[16:20], "big") > 0
        and int.from_bytes(header[20:24], "big") > 0
    )
    if not valid_header:
        raise PackageError("XIAOMI_ICON_INVALID", "Xiaomi icon is not a valid PNG image.")
