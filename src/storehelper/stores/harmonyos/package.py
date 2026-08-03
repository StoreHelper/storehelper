"""Conservative local validation for HarmonyOS APP and HAP artifacts."""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

from storehelper.artifacts.models import ArtifactInfo
from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode

MAX_HARMONYOS_PACKAGE_SIZE = 4 * 1024 * 1024 * 1024
MAX_HARMONYOS_FILE_NAME = 64
_TEMP_PREFIX = re.compile(r"^\d{13}-[0-9a-fA-F]{8}-")
_DASHES = re.compile(r"-+")


class HarmonyOSPackageError(StoreHelperError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, ExitCode.PACKAGE_VALIDATION)


def logical_harmonyos_name(source: str) -> str:
    """Create a deterministic, bounded ASCII upload name."""

    base = source.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(base).suffix.lower()
    if suffix not in (".app", ".hap"):
        suffix = ".app"
    stem = base[: -len(Path(base).suffix)] if Path(base).suffix else base
    stem = _TEMP_PREFIX.sub("", stem)
    normalized = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    safe = [
        character if character.isalnum() or character in "-_." else "-" for character in normalized
    ]
    cleaned = _DASHES.sub("-", "".join(safe)).strip("-._") or "app-release"
    max_stem = MAX_HARMONYOS_FILE_NAME - len(suffix)
    bounded = cleaned[:max_stem].rstrip("-._") or "app-release"
    return f"{bounded}{suffix}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _unsafe_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    member = PurePosixPath(normalized)
    return (
        member.is_absolute()
        or ".." in member.parts
        or (bool(member.parts) and ":" in member.parts[0])
    )


def validate_harmonyos_artifact(path: Path) -> ArtifactInfo:
    """Validate APP/HAP structure without extracting or executing vendor tools."""

    if not path.exists():
        raise HarmonyOSPackageError("PACKAGE_NOT_FOUND", f"Artifact file not found: {path}")
    if not path.is_file() or not os.access(path, os.R_OK):
        raise HarmonyOSPackageError(
            "PACKAGE_NOT_READABLE",
            f"Artifact is not a readable file: {path}",
        )
    size = path.stat().st_size
    if size == 0:
        raise HarmonyOSPackageError("PACKAGE_EMPTY", f"Artifact file is empty: {path}")
    if size > MAX_HARMONYOS_PACKAGE_SIZE:
        raise HarmonyOSPackageError(
            "PACKAGE_TOO_LARGE",
            "HarmonyOS artifacts may not exceed 4 GiB.",
        )

    suffix = path.suffix.lower()
    if suffix not in (".app", ".hap"):
        raise HarmonyOSPackageError(
            "PACKAGE_TYPE_UNSUPPORTED",
            f"HarmonyOS publishing accepts only APP or HAP files: {path.name}",
        )

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            unsafe = next((name for name in names if _unsafe_member(name)), None)
            if unsafe is not None:
                raise HarmonyOSPackageError(
                    "PACKAGE_ARCHIVE_UNSAFE",
                    "Artifact contains an unsafe archive member name.",
                )
            bad_member = archive.testzip()
            if bad_member is not None:
                raise HarmonyOSPackageError(
                    "PACKAGE_ZIP_INVALID",
                    "Artifact contains a corrupt ZIP member.",
                )
    except zipfile.BadZipFile:
        raise HarmonyOSPackageError(
            "PACKAGE_ZIP_INVALID",
            "Artifact is not a valid ZIP archive.",
        ) from None

    if suffix == ".app":
        basenames = {PurePosixPath(name).name.lower() for name in names}
        if "pack.info" not in basenames:
            raise HarmonyOSPackageError(
                "PACKAGE_STRUCTURE_INVALID",
                "APP is missing required entry: pack.info",
            )
        if not any(name.lower().endswith(".hap") for name in names):
            raise HarmonyOSPackageError(
                "PACKAGE_STRUCTURE_INVALID",
                "APP does not contain an embedded HAP.",
            )

    return ArtifactInfo(
        path=path.resolve(),
        kind=suffix[1:],
        size=size,
        sha256=_sha256(path),
        logical_name=logical_harmonyos_name(path.name),
    )
