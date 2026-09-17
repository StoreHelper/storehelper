"""Contract tests for bounded APK/AAB manifest identity inspection."""

from __future__ import annotations

import struct
import warnings
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from storehelper.artifacts.android_metadata import inspect_android_metadata
from storehelper.artifacts.identity import ArtifactIdentity
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.huawei.package import PackageError

ANDROID_NS = "http://schemas.android.com/apk/res/android"


def _varint(value: int) -> bytes:
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _proto_string(number: int, value: str) -> bytes:
    encoded = value.encode("utf-8")
    return _varint(number << 3 | 2) + _varint(len(encoded)) + encoded


def _proto_message(number: int, value: bytes) -> bytes:
    return _varint(number << 3 | 2) + _varint(len(value)) + value


def _aab_attribute(name: str, value: str, namespace: str = "") -> bytes:
    # aapt.pb.XmlAttribute: namespace_uri=1, name=2, value=3.
    return _proto_string(1, namespace) + _proto_string(2, name) + _proto_string(3, value)


def _aab_manifest(attributes: list[bytes], *, root_name: str = "manifest") -> bytes:
    # aapt.pb.XmlNode.element=1; XmlElement.name=3, repeated attribute=4.
    element = _proto_string(3, root_name)
    element += b"".join(_proto_message(4, attribute) for attribute in attributes)
    return _proto_message(1, element)


def _apk_manifest(
    *, package: str = "com.example.demo", code: int = 42, name: str = "1.2.3"
) -> bytes:
    """Construct actual Android binary-XML chunks with a UTF-8 string pool."""
    strings = [
        "manifest",
        "package",
        package,
        ANDROID_NS,
        "android",
        "versionCode",
        "versionName",
        name,
    ]
    offsets = []
    encoded_strings = bytearray()
    for value in strings:
        offsets.append(len(encoded_strings))
        encoded = value.encode("utf-8")
        encoded_strings.extend(bytes((len(value), len(encoded))) + encoded + b"\x00")
    pool_size = 28 + 4 * len(strings) + len(encoded_strings)
    pool = struct.pack("<HHI", 1, 28, pool_size)
    pool += struct.pack("<IIIII", len(strings), 0, 0x100, 28 + 4 * len(strings), 0)
    pool += struct.pack(f"<{len(strings)}I", *offsets) + encoded_strings
    pool += b"\x00" * (-len(pool) % 4)
    pool = pool[:4] + struct.pack("<I", len(pool)) + pool[8:]

    def chunk(kind: int, body: bytes) -> bytes:
        return (
            struct.pack("<HHI", kind, 16, 16 + len(body)) + struct.pack("<II", 1, 0xFFFFFFFF) + body
        )

    namespace = chunk(0x0100, struct.pack("<II", 4, 3))

    def attribute(ns: int, key: int, raw: int, kind: int, data: int) -> bytes:
        return struct.pack("<IIIHBBI", ns, key, raw, 8, 0, kind, data)

    attributes = (
        attribute(0xFFFFFFFF, 1, 2, 3, 2)
        + attribute(3, 5, 0xFFFFFFFF, 0x10, code)
        + attribute(3, 6, 7, 3, 7)
    )
    element = chunk(
        0x0102,
        struct.pack("<IIHHHHHH", 0xFFFFFFFF, 0, 20, 20, 3, 0, 0, 0) + attributes,
    )
    ending = chunk(0x0103, struct.pack("<II", 0xFFFFFFFF, 0))
    namespace_end = chunk(0x0101, struct.pack("<II", 4, 3))
    resource_map = struct.pack("<HHI", 0x0180, 8, 8)
    content = pool + resource_map + namespace + element + ending + namespace_end
    return struct.pack("<HHI", 3, 8, 8 + len(content)) + content


def _zip(path: Path, entries: list[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for member, data in entries:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Duplicate name:", category=UserWarning)
                archive.writestr(member, data)
    return path


def _aab(path: Path, data: bytes) -> Path:
    return _zip(path, [("base/manifest/AndroidManifest.xml", data)])


def _assert_metadata_error(path: Path) -> None:
    with pytest.raises(PackageError) as raised:
        inspect_android_metadata(path)
    assert raised.value.exit_code is ExitCode.PACKAGE_VALIDATION
    assert "manifest" in raised.value.message.lower()
    assert "secret-value" not in raised.value.message


def test_identity_is_frozen_and_validated() -> None:
    identity = ArtifactIdentity(
        package_name="com.example.demo", version_code=42, version_name="1.2"
    )
    assert identity.version_code == 42
    with pytest.raises(ValidationError):
        ArtifactIdentity(package_name="not dotted", version_code=42, version_name=None)
    with pytest.raises(ValidationError):
        ArtifactIdentity(package_name="com.example.demo", version_code=0, version_name=None)
    with pytest.raises(ValidationError):
        identity.version_code = 7


def test_extracts_real_compiled_apk_manifest(tmp_path: Path) -> None:
    path = _zip(tmp_path / "app.apk", [("AndroidManifest.xml", _apk_manifest())])
    assert inspect_android_metadata(path) == ArtifactIdentity(
        package_name="com.example.demo", version_code=42, version_name="1.2.3"
    )


def test_extracts_aab_base_manifest(tmp_path: Path) -> None:
    data = _aab_manifest(
        [
            _aab_attribute("package", "com.example.bundle"),
            _aab_attribute("versionCode", "123", ANDROID_NS),
            _aab_attribute("versionName", "2.0", ANDROID_NS),
        ]
    )
    assert inspect_android_metadata(_aab(tmp_path / "app.aab", data)) == ArtifactIdentity(
        package_name="com.example.bundle", version_code=123, version_name="2.0"
    )


@pytest.mark.parametrize(
    "suffix,member", [("apk", "AndroidManifest.xml"), ("aab", "base/manifest/AndroidManifest.xml")]
)
def test_sparse_placeholder_is_unavailable(tmp_path: Path, suffix: str, member: str) -> None:
    path = _zip(tmp_path / f"app.{suffix}", [(member, b"binary-manifest")])
    assert inspect_android_metadata(path) is None


@pytest.mark.parametrize("code", ["0", "-1", "nope"])
def test_aab_rejects_invalid_version_code(tmp_path: Path, code: str) -> None:
    data = _aab_manifest(
        [
            _aab_attribute("package", "com.example.demo"),
            _aab_attribute("versionCode", code, ANDROID_NS),
        ]
    )
    _assert_metadata_error(_aab(tmp_path / "app.aab", data))


def test_aab_rejects_missing_package_and_wrong_namespace(tmp_path: Path) -> None:
    data = _aab_manifest(
        [
            _aab_attribute("package", "com.example.demo", ANDROID_NS),
            _aab_attribute("versionCode", "42", ANDROID_NS),
        ]
    )
    _assert_metadata_error(_aab(tmp_path / "app.aab", data))


def test_aab_rejects_duplicate_root_attribute(tmp_path: Path) -> None:
    data = _aab_manifest(
        [
            _aab_attribute("package", "com.example.one"),
            _aab_attribute("package", "com.example.two"),
            _aab_attribute("versionCode", "42", ANDROID_NS),
        ]
    )
    _assert_metadata_error(_aab(tmp_path / "app.aab", data))


def test_aab_rejects_unresolved_version_name_reference(tmp_path: Path) -> None:
    data = _aab_manifest(
        [
            _aab_attribute("package", "com.example.demo"),
            _aab_attribute("versionCode", "42", ANDROID_NS),
            _aab_attribute("versionName", "@string/secret-value", ANDROID_NS),
        ]
    )
    _assert_metadata_error(_aab(tmp_path / "app.aab", data))


def test_aab_rejects_recognized_malformed_proto(tmp_path: Path) -> None:
    _assert_metadata_error(_aab(tmp_path / "app.aab", b"\x0a\x80"))


def test_aab_rejects_wrong_root(tmp_path: Path) -> None:
    _assert_metadata_error(_aab(tmp_path / "app.aab", _aab_manifest([], root_name="application")))


@pytest.mark.parametrize(
    "suffix,member", [("apk", "AndroidManifest.xml"), ("aab", "base/manifest/AndroidManifest.xml")]
)
def test_duplicate_zip_manifest_is_rejected(tmp_path: Path, suffix: str, member: str) -> None:
    path = _zip(tmp_path / f"app.{suffix}", [(member, b"placeholder"), (member, b"placeholder")])
    _assert_metadata_error(path)


def test_oversized_manifest_is_rejected_before_reading_all_bytes(tmp_path: Path) -> None:
    data = b"\x0a" + b"x" * (8 * 1024 * 1024 + 1)
    _assert_metadata_error(_aab(tmp_path / "app.aab", data))
