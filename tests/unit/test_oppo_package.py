from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from storehelper.stores.huawei.package import PackageError
from storehelper.stores.oppo.package import MAX_OPPO_PACKAGE_SIZE, validate_oppo_artifact


def test_oppo_artifact_accepts_a_valid_signed_apk(tmp_path: Path) -> None:
    apk = tmp_path / "wallet.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")

    artifact = validate_oppo_artifact(apk)

    assert artifact.kind == "apk"
    assert artifact.path == apk.resolve()
    assert artifact.sha256 == hashlib.sha256(apk.read_bytes()).hexdigest()
    assert artifact.md5 == hashlib.md5(apk.read_bytes(), usedforsecurity=False).hexdigest()


def test_oppo_artifact_rejects_aab_before_archive_inspection(tmp_path: Path) -> None:
    aab = tmp_path / "wallet.aab"
    aab.write_bytes(b"not-read")

    with pytest.raises(PackageError) as raised:
        validate_oppo_artifact(aab)

    assert raised.value.code == "PACKAGE_TYPE_UNSUPPORTED"
    assert "OPPO" in raised.value.message


def test_oppo_artifact_rejects_documented_two_gibibyte_limit(tmp_path: Path) -> None:
    apk = tmp_path / "wallet.apk"
    with apk.open("wb") as package:
        package.truncate(MAX_OPPO_PACKAGE_SIZE + 1)

    with pytest.raises(PackageError) as raised:
        validate_oppo_artifact(apk)

    assert raised.value.code == "PACKAGE_TOO_LARGE"
    assert "2 GiB" in raised.value.message
