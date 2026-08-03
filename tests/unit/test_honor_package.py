from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from storehelper.stores.honor.package import MAX_HONOR_PACKAGE_SIZE, validate_honor_artifact
from storehelper.stores.huawei.package import PackageError


def test_honor_artifact_accepts_valid_signed_apk(tmp_path: Path) -> None:
    apk = tmp_path / "wallet.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")

    artifact = validate_honor_artifact(apk)

    assert artifact.kind == "apk"
    assert artifact.path == apk.resolve()
    assert artifact.sha256 == hashlib.sha256(apk.read_bytes()).hexdigest()


def test_honor_artifact_rejects_aab_before_archive_inspection(tmp_path: Path) -> None:
    aab = tmp_path / "wallet.aab"
    aab.write_bytes(b"not-read")

    with pytest.raises(PackageError) as raised:
        validate_honor_artifact(aab)

    assert raised.value.code == "PACKAGE_TYPE_UNSUPPORTED"
    assert "HONOR" in raised.value.message


def test_honor_artifact_requires_size_strictly_below_four_gibibytes(tmp_path: Path) -> None:
    apk = tmp_path / "wallet.apk"
    with apk.open("wb") as package:
        package.truncate(MAX_HONOR_PACKAGE_SIZE)

    with pytest.raises(PackageError) as raised:
        validate_honor_artifact(apk)

    assert raised.value.code == "PACKAGE_TOO_LARGE"
    assert "smaller than 4 GiB" in raised.value.message
