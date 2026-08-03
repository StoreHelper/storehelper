from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from storehelper.stores.huawei.package import PackageError
from storehelper.stores.models import StoreName, StoreTarget
from storehelper.stores.xiaomi.package import (
    MAX_XIAOMI_PACKAGE_SIZE,
    validate_xiaomi_artifact,
    validate_xiaomi_target,
)


def _target(icon: Path | None) -> StoreTarget:
    return StoreTarget(
        store=StoreName.XIAOMI,
        label="Xiaomi App Store",
        app_id="com.example.wallet",
        package_name="com.example.wallet",
        credential_profile="xiaomi-release",
        language="zh-CN",
        app_name="Example Wallet",
        icon_path=icon,
        privacy_url="https://example.com/privacy",
    )


def test_xiaomi_artifact_accepts_a_valid_apk(tmp_path: Path) -> None:
    apk = tmp_path / "wallet.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")

    artifact = validate_xiaomi_artifact(apk)

    assert artifact.kind == "apk"
    assert artifact.path == apk.resolve()


def test_xiaomi_artifact_rejects_aab_before_archive_inspection(tmp_path: Path) -> None:
    aab = tmp_path / "wallet.aab"
    aab.write_bytes(b"not-read")

    with pytest.raises(PackageError) as raised:
        validate_xiaomi_artifact(aab)

    assert raised.value.code == "PACKAGE_TYPE_UNSUPPORTED"
    assert "Xiaomi" in raised.value.message


def test_xiaomi_artifact_rejects_documented_two_gibibyte_limit(tmp_path: Path) -> None:
    apk = tmp_path / "wallet.apk"
    with apk.open("wb") as package:
        package.truncate(MAX_XIAOMI_PACKAGE_SIZE + 1)

    with pytest.raises(PackageError) as raised:
        validate_xiaomi_artifact(apk)

    assert raised.value.code == "PACKAGE_TOO_LARGE"
    assert "2 GiB" in raised.value.message


@pytest.mark.parametrize(
    ("name", "contents", "code"),
    [
        ("icon.jpg", b"image", "XIAOMI_ICON_INVALID"),
        ("icon.png", b"not-a-png", "XIAOMI_ICON_INVALID"),
    ],
)
def test_xiaomi_icon_preflight_rejects_invalid_local_input(
    tmp_path: Path,
    name: str,
    contents: bytes,
    code: str,
) -> None:
    icon = tmp_path / name
    icon.write_bytes(contents)

    with pytest.raises(PackageError) as raised:
        validate_xiaomi_target(_target(icon))

    assert raised.value.code == code


def test_xiaomi_icon_preflight_rejects_missing_icon() -> None:
    with pytest.raises(PackageError) as raised:
        validate_xiaomi_target(_target(None))

    assert raised.value.code == "XIAOMI_ICON_MISSING"
