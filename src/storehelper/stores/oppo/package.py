"""Local OPPO APK validation built on the hardened Android validator."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import Field

from storehelper.stores.huawei.package import PackageError, PackageInfo, validate_package

MAX_OPPO_PACKAGE_SIZE = 2 * 1024 * 1024 * 1024


class OppoArtifactInfo(PackageInfo):
    """Validated APK values including OPPO's protocol-required MD5 identity."""

    md5: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    try:
        with path.open("rb") as package:
            while chunk := package.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        raise PackageError(
            "PACKAGE_NOT_READABLE", "The OPPO APK became unreadable during validation."
        ) from None
    return digest.hexdigest()


def validate_oppo_artifact(path: Path) -> OppoArtifactInfo:
    """Accept one valid APK within OPPO's supported upload limit."""

    if path.suffix.lower() != ".apk":
        raise PackageError(
            "PACKAGE_TYPE_UNSUPPORTED",
            f"OPPO publishing accepts only APK files: {path.name}",
        )
    try:
        size = path.stat().st_size
    except OSError:
        raise PackageError("PACKAGE_NOT_READABLE", "The OPPO APK is not a readable file.") from None
    if size > MAX_OPPO_PACKAGE_SIZE:
        raise PackageError("PACKAGE_TOO_LARGE", "OPPO APKs may not exceed 2 GiB.")
    artifact = validate_package(path)
    return OppoArtifactInfo.model_validate({**artifact.model_dump(), "md5": _md5(path)})
