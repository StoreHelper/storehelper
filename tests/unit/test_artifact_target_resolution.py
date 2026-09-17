"""Optional YAML identity fields bind to the selected local artifact."""

from __future__ import annotations

from pathlib import Path

import pytest

from storehelper.artifacts.identity import ArtifactIdentity
from storehelper.artifacts.inspect import inspect_artifact_identity
from storehelper.artifacts.models import AppleArtifactInfo
from storehelper.config.loader import ConfigError, load_config, resolve_store_target
from storehelper.stores.models import StoreName


def _config(tmp_path: Path, *, stores: str, package: str = "") -> Path:
    path = tmp_path / "storehelper.yaml"
    path.write_text(
        "version: 1\napps:\n  demo:\n"
        + (f"    package_name: {package}\n" if package else "")
        + "    stores:\n"
        + stores,
        encoding="utf-8",
    )
    return path


HUAWEI = """      huawei:
        app_id: '123456'
        credential_profile: release
"""
HARMONY = """      harmonyos:
        app_id: '234567'
        credential_profile: release
"""
APPLE = """      apple:
        app_id: '345678'
        app_store_version_id: version-resource
        credential_profile: release
"""
OPPO = """      oppo:
        credential_profile: release
"""


def test_minimal_huawei_config_gets_package_from_artifact(tmp_path: Path) -> None:
    application = load_config(_config(tmp_path, stores=HUAWEI)).apps["demo"]
    assert application.package_name is None
    target = resolve_store_target(
        application,
        StoreName.HUAWEI,
        ArtifactIdentity(package_name="com.example.demo", version_code=42),
    )
    assert target.app_id == "123456"
    assert target.package_name == "com.example.demo"


def test_apple_only_needs_no_dummy_android_package_or_bundle(tmp_path: Path) -> None:
    application = load_config(_config(tmp_path, stores=APPLE)).apps["demo"]
    assert application.package_name is None
    assert application.stores.apple.bundle_id is None
    target = resolve_store_target(
        application, StoreName.APPLE, ArtifactIdentity(package_name="com.example.ios")
    )
    assert target.package_name == "com.example.ios"
    assert target.app_id == "345678"
    assert target.release_id == "version-resource"


def test_harmony_uses_store_local_package_not_android_package(tmp_path: Path) -> None:
    application = load_config(
        _config(tmp_path, stores=HUAWEI + HARMONY, package="com.example.android")
    ).apps["demo"]
    target = resolve_store_target(
        application,
        StoreName.HARMONYOS,
        ArtifactIdentity(package_name="com.example.harmony", version_code=7),
    )
    assert target.package_name == "com.example.harmony"


def test_oppo_version_code_comes_from_artifact(tmp_path: Path) -> None:
    application = load_config(_config(tmp_path, stores=OPPO)).apps["demo"]
    target = resolve_store_target(
        application,
        StoreName.OPPO,
        ArtifactIdentity(package_name="com.example.demo", version_code=42),
    )
    assert target.package_name == target.app_id == "com.example.demo"
    assert target.version_code == 42


@pytest.mark.parametrize(
    ("store", "stores", "field"),
    [
        (StoreName.HUAWEI, HUAWEI, "package_name"),
        (StoreName.HARMONYOS, HARMONY, "package_name"),
        (StoreName.APPLE, APPLE, "bundle_id"),
        (StoreName.OPPO, OPPO, "package_name"),
    ],
)
def test_missing_config_and_artifact_identity_names_field(
    tmp_path: Path, store: StoreName, stores: str, field: str
) -> None:
    application = load_config(_config(tmp_path, stores=stores)).apps["demo"]
    with pytest.raises(ConfigError, match=field):
        resolve_store_target(application, store)


def test_oppo_missing_version_code_names_field(tmp_path: Path) -> None:
    application = load_config(_config(tmp_path, stores=OPPO)).apps["demo"]
    with pytest.raises(ConfigError, match="version_code"):
        resolve_store_target(
            application, StoreName.OPPO, ArtifactIdentity(package_name="com.example.demo")
        )


def test_explicit_package_mismatch_is_rejected(tmp_path: Path) -> None:
    application = load_config(
        _config(tmp_path, stores=HUAWEI, package="com.example.expected")
    ).apps["demo"]
    with pytest.raises(ConfigError, match="package_name") as raised:
        resolve_store_target(
            application,
            StoreName.HUAWEI,
            ArtifactIdentity(package_name="com.example.actual"),
        )
    assert raised.value.code == "CONFIG_ARTIFACT_MISMATCH"


def test_explicit_version_code_mismatch_is_rejected(tmp_path: Path) -> None:
    application = load_config(
        _config(tmp_path, stores=OPPO + "        version_code: 41\n", package="com.example.demo")
    ).apps["demo"]
    with pytest.raises(ConfigError, match="version_code"):
        resolve_store_target(
            application,
            StoreName.OPPO,
            ArtifactIdentity(package_name="com.example.demo", version_code=42),
        )


def test_explicit_config_is_legacy_fallback_when_metadata_unavailable(tmp_path: Path) -> None:
    application = load_config(
        _config(tmp_path, stores=HUAWEI, package="com.example.demo")
    ).apps["demo"]
    assert resolve_store_target(application, StoreName.HUAWEI).package_name == "com.example.demo"


@pytest.mark.parametrize("value", [0, -1, True, "42"])
def test_supplied_version_code_remains_strict(tmp_path: Path, value: object) -> None:
    data = {
        "version": 1,
        "apps": {
            "demo": {
                "stores": {"oppo": {"credential_profile": "release", "version_code": value}}
            }
        },
    }
    from pydantic import ValidationError

    from storehelper.config.models import StoreHelperConfig

    with pytest.raises(ValidationError):
        StoreHelperConfig.model_validate(data)


def test_dispatcher_reuses_validated_ipa_info(tmp_path: Path) -> None:
    package = tmp_path / "release.ipa"
    artifact = AppleArtifactInfo(
        path=package,
        kind="ipa",
        size=10,
        sha256="a" * 64,
        logical_name="release.ipa",
        bundle_id="com.example.ios",
        marketing_version="1.2",
        build_version="42",
    )
    assert inspect_artifact_identity(package, StoreName.APPLE, artifact) == ArtifactIdentity(
        package_name="com.example.ios", version_name="1.2"
    )
