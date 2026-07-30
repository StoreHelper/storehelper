"""Local APK/AAB validation and Huawei-safe logical naming."""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import zipfile
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode

MAX_PACKAGE_SIZE = 4 * 1024 * 1024 * 1024
MAX_HUAWEI_FILE_NAME = 64
_TEMP_PREFIX = re.compile(r"^\d{13}-[0-9a-fA-F]{8}-")
_DASHES = re.compile(r"-+")
_APK_SIGNATURE = re.compile(r"^META-INF/[^/]+\.(?:RSA|DSA|EC)$", re.IGNORECASE)


class PackageError(StoreHelperError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, ExitCode.PACKAGE_VALIDATION)


class PackageKind(StrEnum):
    APK = "apk"
    AAB = "aab"


class PackageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    kind: PackageKind
    size: int
    sha256: str
    logical_name: str
    warnings: tuple[str, ...] = ()


def logical_package_name(source: str) -> str:
    """Create one deterministic Huawei file name no longer than 64 code points."""

    base = source.replace("\\", "/").rsplit("/", 1)[-1]
    detected_suffix = Path(base).suffix.lower()
    if base.lower() in (".apk", ".aab"):
        detected_suffix = base.lower()
        stem = ""
    else:
        stem = base[: -len(detected_suffix)] if detected_suffix else base
    suffix = detected_suffix
    if suffix not in (".apk", ".aab"):
        suffix = ".apk"
    stem = _TEMP_PREFIX.sub("", stem)

    safe: list[str] = []
    for character in unicodedata.normalize("NFKC", stem):
        if character.isspace():
            safe.append("-")
        elif character.isalnum() or character in ("-", "_", "."):
            safe.append(character)
        elif unicodedata.category(character).startswith("C"):
            continue
    normalized = _DASHES.sub("-", "".join(safe)).strip("-._") or "app-release"
    max_stem_length = MAX_HUAWEI_FILE_NAME - len(suffix)
    return f"{normalized[:max_stem_length].rstrip('-._') or 'app-release'}{suffix}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as package:
        while chunk := package.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_package(path: Path) -> PackageInfo:
    """Validate the package boundary without requiring the Android SDK."""

    if not path.exists():
        raise PackageError("PACKAGE_NOT_FOUND", f"Package file not found: {path}")
    if not path.is_file() or not os.access(path, os.R_OK):
        raise PackageError("PACKAGE_NOT_READABLE", f"Package is not a readable file: {path}")
    size = path.stat().st_size
    if size == 0:
        raise PackageError("PACKAGE_EMPTY", f"Package file is empty: {path}")
    if size > MAX_PACKAGE_SIZE:
        raise PackageError("PACKAGE_TOO_LARGE", "Huawei packages may not exceed 4 GiB.")

    suffix = path.suffix.lower()
    if suffix not in (".apk", ".aab"):
        raise PackageError(
            "PACKAGE_TYPE_UNSUPPORTED",
            f"Huawei Android publishing accepts only APK or AAB files: {path.name}",
        )
    kind = PackageKind(suffix[1:])
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise PackageError(
                    "PACKAGE_ZIP_INVALID",
                    f"Package contains a corrupt ZIP member: {bad_member}",
                )
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        raise PackageError("PACKAGE_ZIP_INVALID", "Package is not a valid ZIP archive.") from None

    if kind is PackageKind.AAB:
        required = {"BundleConfig.pb", "base/manifest/AndroidManifest.xml"}
        missing = sorted(required - names)
        if missing:
            raise PackageError(
                "PACKAGE_STRUCTURE_INVALID",
                f"AAB is missing required entry: {missing[0]}",
            )
    else:
        if "AndroidManifest.xml" not in names:
            raise PackageError(
                "PACKAGE_STRUCTURE_INVALID",
                "APK is missing required entry: AndroidManifest.xml",
            )
        if not any(_APK_SIGNATURE.fullmatch(name) for name in names):
            warnings.append(
                "APK has no legacy META-INF signing entry; v2/v3 signing may still be valid."
            )

    return PackageInfo(
        path=path.resolve(),
        kind=kind,
        size=size,
        sha256=_sha256(path),
        logical_name=logical_package_name(path.name),
        warnings=tuple(warnings),
    )
