"""Google Play APK/AAB validation built on the hardened Android validator."""

from __future__ import annotations

from pathlib import Path

from storehelper.stores.huawei.package import PackageInfo as PackageInfo
from storehelper.stores.huawei.package import validate_package

__all__ = ["PackageInfo", "validate_google_play_artifact"]


def validate_google_play_artifact(path: Path) -> PackageInfo:
    """Validate an Android package locally before Google performs authoritative checks."""

    return validate_package(path)
