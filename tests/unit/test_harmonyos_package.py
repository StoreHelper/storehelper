from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from storehelper.artifacts.models import ArtifactInfo
from storehelper.stores.harmonyos.package import (
    MAX_HARMONYOS_PACKAGE_SIZE,
    HarmonyOSPackageError,
    logical_harmonyos_name,
    validate_harmonyos_artifact,
)


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


def test_validates_app_structure_and_streams_digest(tmp_path: Path) -> None:
    path = _zip(
        tmp_path / "1785240000000-12ab34cd-wallet.app",
        {"pack.info": b"{}", "entry-default-signed.hap": b"hap"},
    )

    artifact = validate_harmonyos_artifact(path)

    assert isinstance(artifact, ArtifactInfo)
    assert artifact.kind == "app"
    assert artifact.size == path.stat().st_size
    assert artifact.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert artifact.logical_name == "wallet.app"


def test_validates_hap_without_assuming_manifest_layout(tmp_path: Path) -> None:
    path = _zip(tmp_path / "entry.hap", {"resources.index": b"index"})

    artifact = validate_harmonyos_artifact(path)

    assert artifact.kind == "hap"
    assert artifact.logical_name == "entry.hap"


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ({"entry.hap": b"hap"}, "pack.info"),
        ({"pack.info": b"{}"}, "embedded HAP"),
    ],
)
def test_app_requires_pack_info_and_embedded_hap(
    tmp_path: Path,
    entries: dict[str, bytes],
    message: str,
) -> None:
    path = _zip(tmp_path / "invalid.app", entries)

    with pytest.raises(HarmonyOSPackageError, match=message):
        validate_harmonyos_artifact(path)


@pytest.mark.parametrize("member", ["../outside", "/absolute", r"dir\..\outside"])
def test_rejects_unsafe_archive_member_names(tmp_path: Path, member: str) -> None:
    path = _zip(tmp_path / "unsafe.hap", {member: b"bad"})

    with pytest.raises(HarmonyOSPackageError) as raised:
        validate_harmonyos_artifact(path)

    assert raised.value.code == "PACKAGE_ARCHIVE_UNSAFE"


@pytest.mark.parametrize("name", ["release.apk", "release.zip", "release.ipa"])
def test_rejects_unsupported_suffix(tmp_path: Path, name: str) -> None:
    path = _zip(tmp_path / name, {"anything": b"value"})

    with pytest.raises(HarmonyOSPackageError) as raised:
        validate_harmonyos_artifact(path)

    assert raised.value.code == "PACKAGE_TYPE_UNSUPPORTED"


def test_rejects_empty_and_corrupt_archives(tmp_path: Path) -> None:
    empty = tmp_path / "empty.hap"
    empty.write_bytes(b"")
    corrupt = tmp_path / "corrupt.app"
    corrupt.write_bytes(b"not-a-zip")

    with pytest.raises(HarmonyOSPackageError) as empty_error:
        validate_harmonyos_artifact(empty)
    with pytest.raises(HarmonyOSPackageError) as corrupt_error:
        validate_harmonyos_artifact(corrupt)

    assert empty_error.value.code == "PACKAGE_EMPTY"
    assert corrupt_error.value.code == "PACKAGE_ZIP_INVALID"


def test_rejects_artifact_larger_than_four_gib(tmp_path: Path) -> None:
    path = tmp_path / "huge.app"
    with path.open("wb") as stream:
        stream.truncate(MAX_HARMONYOS_PACKAGE_SIZE + 1)

    with pytest.raises(HarmonyOSPackageError) as raised:
        validate_harmonyos_artifact(path)

    assert raised.value.code == "PACKAGE_TOO_LARGE"


def test_safe_name_is_bounded_ascii_and_preserves_suffix() -> None:
    name = logical_harmonyos_name("1785240000000-12ab34cd-钱包 " + "a" * 100 + ".APP")

    assert name.isascii()
    assert len(name) <= 64
    assert name.endswith(".app")
