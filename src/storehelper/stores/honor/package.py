"""Local HONOR APK validation built on the hardened Android validator."""

from __future__ import annotations

from pathlib import Path

from storehelper.stores.huawei.package import PackageError, PackageInfo, validate_package

MAX_HONOR_PACKAGE_SIZE = 4 * 1024 * 1024 * 1024


def validate_honor_artifact(path: Path) -> PackageInfo:
    """Accept one valid APK strictly smaller than HONOR's documented 4 GiB limit."""

    if path.suffix.lower() != ".apk":
        raise PackageError(
            "PACKAGE_TYPE_UNSUPPORTED",
            f"HONOR App Market publishing accepts only APK files: {path.name}",
        )
    try:
        size = path.stat().st_size
    except OSError:
        raise PackageError(
            "PACKAGE_NOT_READABLE", "The HONOR APK is not a readable file."
        ) from None
    if size >= MAX_HONOR_PACKAGE_SIZE:
        raise PackageError("PACKAGE_TOO_LARGE", "HONOR APKs must be smaller than 4 GiB.")
    return validate_package(path)
