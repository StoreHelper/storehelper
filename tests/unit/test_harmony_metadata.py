"""HarmonyOS identity is read only from bounded, unambiguous root metadata."""

from __future__ import annotations

import json
import warnings
import zipfile
from pathlib import Path

import pytest

from storehelper.artifacts.harmony_metadata import inspect_harmony_metadata
from storehelper.artifacts.identity import ArtifactIdentity
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.harmonyos.package import HarmonyOSPackageError


def _archive(path: Path, entries: list[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Duplicate name:", category=UserWarning)
                archive.writestr(name, content)
    return path


def _app_metadata(*, code: object = 42, name: object = "1.2.3") -> bytes:
    return json.dumps(
        {
            "summary": {
                "app": {
                    "bundleName": "com.example.harmony",
                    "version": {"code": code, "name": name},
                }
            }
        }
    ).encode()


def _hap_metadata(*, code: object = 42, name: object = "1.2.3") -> bytes:
    return json.dumps(
        {
            "app": {
                "bundleName": "com.example.harmony",
                "versionCode": code,
                "versionName": name,
            }
        }
    ).encode()


def _assert_invalid(path: Path) -> None:
    with pytest.raises(HarmonyOSPackageError) as raised:
        inspect_harmony_metadata(path)
    assert raised.value.exit_code is ExitCode.PACKAGE_VALIDATION
    assert "metadata" in raised.value.message.lower()


def test_app_uses_root_pack_info_summary(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "release.app",
        [("pack.info", _app_metadata()), ("entry-default.hap", b"opaque")],
    )
    assert inspect_harmony_metadata(path) == ArtifactIdentity(
        package_name="com.example.harmony", version_code=42, version_name="1.2.3"
    )


def test_hap_uses_root_module_json(tmp_path: Path) -> None:
    path = _archive(tmp_path / "entry.hap", [("module.json", _hap_metadata())])
    assert inspect_harmony_metadata(path) == ArtifactIdentity(
        package_name="com.example.harmony", version_code=42, version_name="1.2.3"
    )


@pytest.mark.parametrize(
    ("suffix", "entry"),
    [("app", "pack.info"), ("hap", "module.json")],
)
def test_sparse_legacy_metadata_is_unavailable(
    tmp_path: Path, suffix: str, entry: str
) -> None:
    path = _archive(tmp_path / f"release.{suffix}", [(entry, b"{}")])
    assert inspect_harmony_metadata(path) is None


def test_nested_decoy_is_not_used(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "release.app",
        [("assets/pack.info", _app_metadata()), ("entry-default.hap", b"opaque")],
    )
    assert inspect_harmony_metadata(path) is None


@pytest.mark.parametrize(
    ("suffix", "entry", "metadata"),
    [("app", "pack.info", _app_metadata()), ("hap", "module.json", _hap_metadata())],
)
def test_duplicate_root_metadata_is_rejected(
    tmp_path: Path, suffix: str, entry: str, metadata: bytes
) -> None:
    path = _archive(tmp_path / f"release.{suffix}", [(entry, metadata), (entry, metadata)])
    _assert_invalid(path)


@pytest.mark.parametrize(
    ("suffix", "entry"),
    [("app", "pack.info"), ("hap", "module.json")],
)
def test_oversized_metadata_is_rejected(tmp_path: Path, suffix: str, entry: str) -> None:
    path = _archive(tmp_path / f"release.{suffix}", [(entry, b"x" * (1024 * 1024 + 1))])
    _assert_invalid(path)


@pytest.mark.parametrize("content", [b"{", b"\xff", b"[]", b'{"app": {}, "app": {}}'])
def test_malformed_or_duplicate_json_is_rejected(tmp_path: Path, content: bytes) -> None:
    path = _archive(tmp_path / "entry.hap", [("module.json", content)])
    _assert_invalid(path)


@pytest.mark.parametrize("code", [0, -1, True, "42", None])
def test_invalid_hap_version_code_is_rejected(tmp_path: Path, code: object) -> None:
    path = _archive(tmp_path / "entry.hap", [("module.json", _hap_metadata(code=code))])
    _assert_invalid(path)


def test_invalid_app_version_shape_is_rejected(tmp_path: Path) -> None:
    path = _archive(tmp_path / "release.app", [("pack.info", _app_metadata(name=7))])
    _assert_invalid(path)
