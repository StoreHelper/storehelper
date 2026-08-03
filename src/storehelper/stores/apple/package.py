"""Read-only IPA validation and primary-bundle inspection."""

from __future__ import annotations

import hashlib
import os
import plistlib
import re
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

from storehelper.artifacts.models import AppleArtifactInfo
from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode

MAX_IPA_SIZE = (1 << 63) - 1
MAX_PLIST_SIZE = 10 * 1024 * 1024
MAX_APPLE_FILE_NAME = 255
_DASHES = re.compile(r"-+")


class ApplePackageError(StoreHelperError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, ExitCode.PACKAGE_VALIDATION)


def logical_ipa_name(source: str) -> str:
    base = source.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = ".ipa"
    source_suffix = Path(base).suffix
    stem = base[: -len(source_suffix)] if source_suffix else base
    normalized = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    safe = [
        character if character.isalnum() or character in "-_." else "-" for character in normalized
    ]
    cleaned = _DASHES.sub("-", "".join(safe)).strip("-._") or "app-release"
    max_stem = MAX_APPLE_FILE_NAME - len(suffix)
    return f"{cleaned[:max_stem].rstrip('-._') or 'app-release'}{suffix}"


def _unsafe_member(name: str) -> bool:
    member = PurePosixPath(name.replace("\\", "/"))
    return (
        member.is_absolute()
        or ".." in member.parts
        or (bool(member.parts) and ":" in member.parts[0])
    )


def _primary_plist(infos: list[zipfile.ZipInfo]) -> zipfile.ZipInfo:
    candidates: list[zipfile.ZipInfo] = []
    for info in infos:
        parts = PurePosixPath(info.filename.replace("\\", "/")).parts
        if (
            len(parts) == 3
            and parts[0] == "Payload"
            and parts[1].endswith(".app")
            and parts[2] == "Info.plist"
        ):
            candidates.append(info)
    if len(candidates) != 1:
        raise ApplePackageError(
            "APPLE_PACKAGE_STRUCTURE_INVALID",
            "IPA must contain exactly one top-level Payload application.",
        )
    return candidates[0]


def _metadata(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> tuple[str, str, str]:
    if info.file_size > MAX_PLIST_SIZE:
        raise ApplePackageError(
            "APPLE_PACKAGE_PLIST_INVALID",
            "The primary application Info.plist is too large.",
        )
    try:
        with archive.open(info) as stream:
            raw = stream.read(MAX_PLIST_SIZE + 1)
        if len(raw) > MAX_PLIST_SIZE:
            raise ValueError
        value = plistlib.loads(raw)
    except (KeyError, OSError, ValueError, plistlib.InvalidFileException):
        raise ApplePackageError(
            "APPLE_PACKAGE_PLIST_INVALID",
            "The primary application Info.plist is invalid.",
        ) from None
    if not isinstance(value, dict):
        raise ApplePackageError(
            "APPLE_PACKAGE_PLIST_INVALID",
            "The primary application Info.plist is invalid.",
        )
    fields = (
        value.get("CFBundleIdentifier"),
        value.get("CFBundleShortVersionString"),
        value.get("CFBundleVersion"),
    )
    if not all(isinstance(item, str) and item.strip() for item in fields):
        raise ApplePackageError(
            "APPLE_PACKAGE_METADATA_INVALID",
            "IPA bundle ID, marketing version, and build version are required.",
        )
    bundle_id, marketing_version, build_version = fields
    assert isinstance(bundle_id, str)
    assert isinstance(marketing_version, str)
    assert isinstance(build_version, str)
    return bundle_id.strip(), marketing_version.strip(), build_version.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_ipa(path: Path) -> AppleArtifactInfo:
    """Inspect one IPA without extracting or executing archive contents."""

    if not path.exists():
        raise ApplePackageError("APPLE_PACKAGE_NOT_FOUND", f"IPA file not found: {path}")
    if not path.is_file() or not os.access(path, os.R_OK):
        raise ApplePackageError(
            "APPLE_PACKAGE_NOT_READABLE",
            f"IPA is not a readable file: {path}",
        )
    if path.suffix.lower() != ".ipa":
        raise ApplePackageError(
            "APPLE_PACKAGE_TYPE_UNSUPPORTED",
            "Apple publishing accepts only IPA files.",
        )
    size = path.stat().st_size
    if size == 0:
        raise ApplePackageError("APPLE_PACKAGE_EMPTY", f"IPA file is empty: {path}")
    if size > MAX_IPA_SIZE:
        raise ApplePackageError(
            "APPLE_PACKAGE_TOO_LARGE",
            "IPA size exceeds the App Store Connect API limit.",
        )

    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if any(_unsafe_member(info.filename) for info in infos):
                raise ApplePackageError(
                    "APPLE_PACKAGE_ARCHIVE_UNSAFE",
                    "IPA contains an unsafe archive member name.",
                )
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ApplePackageError(
                    "APPLE_PACKAGE_ZIP_INVALID",
                    "IPA contains a corrupt ZIP member.",
                )
            bundle_id, marketing_version, build_version = _metadata(
                archive,
                _primary_plist(infos),
            )
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        raise ApplePackageError(
            "APPLE_PACKAGE_ZIP_INVALID",
            "IPA is not a valid ZIP archive.",
        ) from None

    return AppleArtifactInfo(
        path=path.resolve(),
        kind="ipa",
        size=size,
        sha256=_sha256(path),
        logical_name=logical_ipa_name(path.name),
        bundle_id=bundle_id,
        marketing_version=marketing_version,
        build_version=build_version,
        platform="IOS",
    )
