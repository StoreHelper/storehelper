"""Bounded identity inspection for HarmonyOS APP and HAP artifacts."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from storehelper.artifacts.identity import ANDROID_PACKAGE_NAME, ArtifactIdentity
from storehelper.stores.harmonyos.package import HarmonyOSPackageError

_MAX_METADATA_SIZE = 1024 * 1024


def _invalid(reason: str) -> HarmonyOSPackageError:
    return HarmonyOSPackageError("PACKAGE_METADATA_INVALID", f"HarmonyOS metadata {reason}.")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_metadata(path: Path) -> object | None:
    suffix = path.suffix.lower()
    if suffix == ".app":
        member_name = "pack.info"
    elif suffix == ".hap":
        member_name = "module.json"
    else:
        raise _invalid("requires an APP or HAP file")
    try:
        with zipfile.ZipFile(path) as archive:
            members = [info for info in archive.infolist() if info.filename == member_name]
            if not members:
                return None
            if len(members) != 1:
                raise _invalid("contains duplicate root metadata members")
            member = members[0]
            if member.file_size > _MAX_METADATA_SIZE:
                raise _invalid("exceeds the 1 MiB size limit")
            with archive.open(member) as source:
                data = source.read(_MAX_METADATA_SIZE + 1)
            if len(data) > _MAX_METADATA_SIZE:
                raise _invalid("exceeds the 1 MiB size limit")
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, ValueError) as error:
        raise _invalid("could not be read") from error
    try:
        parsed: object = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs)
        return parsed
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise _invalid("is not unambiguous UTF-8 JSON") from error


def _identity(package: object, code: object, name: object) -> ArtifactIdentity:
    if not isinstance(package, str) or not ANDROID_PACKAGE_NAME.fullmatch(package):
        raise _invalid("is missing a valid bundleName")
    if type(code) is not int or code <= 0:
        raise _invalid("is missing a positive integer version code")
    if name is not None and (not isinstance(name, str) or not name):
        raise _invalid("has an invalid version name")
    try:
        return ArtifactIdentity(package_name=package, version_code=code, version_name=name)
    except ValueError as error:
        raise _invalid("contains an invalid bundle or version") from error


def inspect_harmony_metadata(path: Path) -> ArtifactIdentity | None:
    """Return exact root package identity, or None for sparse legacy archives."""
    metadata = _read_metadata(path)
    if metadata is None or metadata == {}:
        return None
    if not isinstance(metadata, dict):
        raise _invalid("must be a JSON object")
    if path.suffix.lower() == ".app":
        summary = metadata.get("summary")
        if not isinstance(summary, dict):
            raise _invalid("is missing summary.app")
        app = summary.get("app")
        if not isinstance(app, dict):
            raise _invalid("is missing summary.app")
        version = app.get("version")
        if not isinstance(version, dict):
            raise _invalid("is missing summary.app.version")
        return _identity(app.get("bundleName"), version.get("code"), version.get("name"))
    app = metadata.get("app")
    if not isinstance(app, dict):
        raise _invalid("is missing app")
    return _identity(app.get("bundleName"), app.get("versionCode"), app.get("versionName"))
