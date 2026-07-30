from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from storehelper.stores.huawei.package import (
    PackageError,
    PackageKind,
    logical_package_name,
    validate_package,
)


def make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1785240000000-12ab34cd-app-release.apk", "app-release.apk"),
        ("../../bad\x00name.aab", "badname.aab"),
        (r"C:\\build\\my app.apk", "my-app.apk"),
        ("////.apk", "app-release.apk"),
    ],
)
def test_logical_package_name(source: str, expected: str) -> None:
    assert logical_package_name(source) == expected


def test_logical_name_is_at_most_64_characters_and_preserves_suffix() -> None:
    name = logical_package_name("a" * 100 + ".aab")

    assert len(name) == 64
    assert name.endswith(".aab")


def test_validates_apk_and_streams_sha256(tmp_path: Path) -> None:
    path = make_zip(
        tmp_path / "release.apk",
        {
            "AndroidManifest.xml": b"binary-manifest",
            "classes.dex": b"dex",
            "META-INF/CERT.RSA": b"signature",
        },
    )

    package = validate_package(path)

    assert package.kind is PackageKind.APK
    assert package.size == path.stat().st_size
    assert package.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert package.logical_name == "release.apk"
    assert package.warnings == ()


def test_apk_without_legacy_signature_metadata_is_a_warning(tmp_path: Path) -> None:
    path = make_zip(tmp_path / "release.apk", {"AndroidManifest.xml": b"manifest"})

    package = validate_package(path)

    assert package.kind is PackageKind.APK
    assert package.warnings == (
        "APK has no legacy META-INF signing entry; v2/v3 signing may still be valid.",
    )


def test_aab_requires_bundle_and_base_manifest(tmp_path: Path) -> None:
    path = make_zip(tmp_path / "app.aab", {"BundleConfig.pb": b"bundle"})

    with pytest.raises(PackageError, match=r"base/manifest/AndroidManifest\.xml"):
        validate_package(path)


def test_validates_minimal_aab(tmp_path: Path) -> None:
    path = make_zip(
        tmp_path / "app.aab",
        {
            "BundleConfig.pb": b"bundle",
            "base/manifest/AndroidManifest.xml": b"manifest",
        },
    )

    package = validate_package(path)

    assert package.kind is PackageKind.AAB


@pytest.mark.parametrize("name", ["release.zip", "release.ipa"])
def test_rejects_unsupported_extensions(tmp_path: Path, name: str) -> None:
    path = make_zip(tmp_path / name, {"AndroidManifest.xml": b"manifest"})

    with pytest.raises(PackageError) as raised:
        validate_package(path)

    assert raised.value.code == "PACKAGE_TYPE_UNSUPPORTED"


def test_rejects_invalid_zip(tmp_path: Path) -> None:
    path = tmp_path / "broken.apk"
    path.write_bytes(b"not-a-zip")

    with pytest.raises(PackageError) as raised:
        validate_package(path)

    assert raised.value.code == "PACKAGE_ZIP_INVALID"
