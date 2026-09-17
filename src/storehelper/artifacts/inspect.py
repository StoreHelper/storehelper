"""Dispatch artifact identity inspection without revalidating known IPA metadata."""

from __future__ import annotations

from pathlib import Path

from storehelper.artifacts.android_metadata import inspect_android_metadata
from storehelper.artifacts.harmony_metadata import inspect_harmony_metadata
from storehelper.artifacts.identity import ArtifactIdentity
from storehelper.artifacts.models import AppleArtifactInfo, ArtifactInfo
from storehelper.stores.apple.package import validate_ipa
from storehelper.stores.models import StoreName

_ANDROID_STORES = frozenset(
    {
        StoreName.HUAWEI,
        StoreName.GOOGLE_PLAY,
        StoreName.XIAOMI,
        StoreName.OPPO,
        StoreName.VIVO,
        StoreName.HONOR,
    }
)


def inspect_artifact_identity(
    path: Path,
    store: StoreName,
    artifact: ArtifactInfo | None = None,
) -> ArtifactIdentity | None:
    """Return local identity, or None for a legacy opaque Android/Harmony package."""
    if store in _ANDROID_STORES:
        return inspect_android_metadata(path)
    if store is StoreName.HARMONYOS:
        return inspect_harmony_metadata(path)
    if store is StoreName.APPLE:
        ipa = artifact if isinstance(artifact, AppleArtifactInfo) else validate_ipa(path)
        return ArtifactIdentity(package_name=ipa.bundle_id, version_name=ipa.marketing_version)
    raise ValueError(f"Unsupported store for artifact inspection: {store}")
