"""Local OPPO APK validation built on the hardened Android validator."""

from __future__ import annotations

from pathlib import Path

from storehelper.artifacts.models import ArtifactInfo
from storehelper.stores.huawei.package import PackageError, validate_package

MAX_OPPO_PACKAGE_SIZE = 2 * 1024 * 1024 * 1024


def validate_oppo_artifact(path: Path) -> ArtifactInfo:
    """Accept one valid APK within OPPO's supported upload limit."""

    if path.suffix.lower() != ".apk":
        raise PackageError(
            "PACKAGE_TYPE_UNSUPPORTED",
            f"OPPO publishing accepts only APK files: {path.name}",
        )
    try:
        size = path.stat().st_size
    except OSError:
        return validate_package(path)
    if size > MAX_OPPO_PACKAGE_SIZE:
        raise PackageError("PACKAGE_TOO_LARGE", "OPPO APKs may not exceed 2 GiB.")
    return validate_package(path)
