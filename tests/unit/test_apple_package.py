from __future__ import annotations

import plistlib
import zipfile
from pathlib import Path

import pytest

from storehelper.stores.apple.package import ApplePackageError, validate_ipa


def _ipa(
    path: Path,
    *,
    plist_format: plistlib.PlistFormat = plistlib.FMT_XML,
    plist: dict[str, object] | None = None,
    app_name: str = "Wallet",
) -> Path:
    values = plist or {
        "CFBundleIdentifier": "com.example.wallet.ios",
        "CFBundleShortVersionString": "1.2.3",
        "CFBundleVersion": "42",
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"Payload/{app_name}.app/Info.plist",
            plistlib.dumps(values, fmt=plist_format),
        )
        archive.writestr(f"Payload/{app_name}.app/Wallet", b"signed-binary")
    return path


@pytest.mark.parametrize("plist_format", [plistlib.FMT_XML, plistlib.FMT_BINARY])
def test_validates_xml_and_binary_ipa_metadata(
    tmp_path: Path,
    plist_format: plistlib.PlistFormat,
) -> None:
    path = _ipa(tmp_path / "Wållét Release.ipa", plist_format=plist_format)

    artifact = validate_ipa(path)

    assert artifact.path == path.resolve()
    assert artifact.kind == "ipa"
    assert artifact.bundle_id == "com.example.wallet.ios"
    assert artifact.marketing_version == "1.2.3"
    assert artifact.build_version == "42"
    assert artifact.platform == "IOS"
    assert artifact.size == path.stat().st_size
    assert len(artifact.sha256) == 64
    assert artifact.logical_name == "Wallet-Release.ipa"


def test_validation_streams_archive_instead_of_reading_entire_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _ipa(tmp_path / "wallet.ipa")

    def forbidden(path: Path) -> bytes:
        raise AssertionError(f"read the full IPA: {path}")

    monkeypatch.setattr(Path, "read_bytes", forbidden)

    assert validate_ipa(path).bundle_id == "com.example.wallet.ios"


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    [
        ("wallet.apk", b"not-an-ipa", "APPLE_PACKAGE_TYPE_UNSUPPORTED"),
        ("wallet.ipa", b"", "APPLE_PACKAGE_EMPTY"),
        ("wallet.ipa", b"not-a-zip", "APPLE_PACKAGE_ZIP_INVALID"),
    ],
)
def test_rejects_wrong_empty_and_corrupt_files(
    tmp_path: Path,
    filename: str,
    content: bytes,
    code: str,
) -> None:
    path = tmp_path / filename
    path.write_bytes(content)

    with pytest.raises(ApplePackageError) as raised:
        validate_ipa(path)

    assert raised.value.code == code


@pytest.mark.parametrize("member", ["../secret", "/absolute", "C:/private"])
def test_rejects_unsafe_archive_member_names(tmp_path: Path, member: str) -> None:
    path = _ipa(tmp_path / "wallet.ipa")
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr(member, b"secret")

    with pytest.raises(ApplePackageError) as raised:
        validate_ipa(path)

    assert raised.value.code == "APPLE_PACKAGE_ARCHIVE_UNSAFE"
    assert member not in str(raised.value)


def test_requires_exactly_one_top_level_application(tmp_path: Path) -> None:
    missing = tmp_path / "missing.ipa"
    with zipfile.ZipFile(missing, "w") as archive:
        archive.writestr("Payload/readme.txt", b"missing")

    multiple = _ipa(tmp_path / "multiple.ipa")
    with zipfile.ZipFile(multiple, "a") as archive:
        archive.writestr(
            "Payload/Other.app/Info.plist",
            plistlib.dumps(
                {
                    "CFBundleIdentifier": "com.example.other",
                    "CFBundleShortVersionString": "1.0",
                    "CFBundleVersion": "1",
                }
            ),
        )

    for path in (missing, multiple):
        with pytest.raises(ApplePackageError) as raised:
            validate_ipa(path)
        assert raised.value.code == "APPLE_PACKAGE_STRUCTURE_INVALID"


def test_rejects_invalid_plist_without_echoing_contents(tmp_path: Path) -> None:
    path = tmp_path / "invalid.ipa"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Payload/Wallet.app/Info.plist", b"private_key=never-print")

    with pytest.raises(ApplePackageError) as raised:
        validate_ipa(path)

    assert raised.value.code == "APPLE_PACKAGE_PLIST_INVALID"
    assert "never-print" not in str(raised.value)


@pytest.mark.parametrize(
    "missing",
    ["CFBundleIdentifier", "CFBundleShortVersionString", "CFBundleVersion"],
)
def test_requires_nonempty_primary_bundle_fields(tmp_path: Path, missing: str) -> None:
    values: dict[str, object] = {
        "CFBundleIdentifier": "com.example.wallet.ios",
        "CFBundleShortVersionString": "1.2.3",
        "CFBundleVersion": "42",
    }
    values[missing] = " "
    path = _ipa(tmp_path / "wallet.ipa", plist=values)

    with pytest.raises(ApplePackageError) as raised:
        validate_ipa(path)

    assert raised.value.code == "APPLE_PACKAGE_METADATA_INVALID"
