from pathlib import Path

import pytest

from storehelper.config.loader import ConfigError, load_config, resolve_store_target
from storehelper.stores.models import StoreName


def _write(tmp_path: Path, stores: str) -> Path:
    path = tmp_path / "storehelper.yaml"
    path.write_text(
        f"""version: 1
apps:
  wallet:
    package_name: com.example.wallet
    stores:
{stores}""",
        encoding="utf-8",
    )
    return path


HUAWEI = """      huawei:
        app_id: '100000001'
        credential_profile: company
"""

HARMONYOS = """      harmonyos:
        app_id: '100000002'
        package_name: com.example.wallet.harmony
        credential_profile: company
        language: en-US
"""


def test_loads_harmonyos_only_application_and_resolves_target(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, HARMONYOS))

    application = config.apps["wallet"]
    target = resolve_store_target(application, StoreName.HARMONYOS)

    assert application.stores.huawei is None
    assert target.model_dump(mode="json") == {
        "store": "harmonyos",
        "label": "Huawei AppGallery (HarmonyOS)",
        "app_id": "100000002",
        "package_name": "com.example.wallet.harmony",
        "credential_profile": "company",
        "language": "en-US",
    }


def test_dual_store_application_keeps_independent_package_names(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, HUAWEI + HARMONYOS))
    application = config.apps["wallet"]

    android = resolve_store_target(application, StoreName.HUAWEI)
    harmonyos = resolve_store_target(application, StoreName.HARMONYOS)

    assert android.package_name == "com.example.wallet"
    assert harmonyos.package_name == "com.example.wallet.harmony"
    assert android.credential_profile == harmonyos.credential_profile == "company"


def test_stores_requires_at_least_one_configured_store(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="at least one"):
        load_config(_write(tmp_path, "      {}\n"))


def test_harmonyos_package_name_must_be_dotted(tmp_path: Path) -> None:
    invalid = HARMONYOS.replace("com.example.wallet.harmony", "not-a-bundle")

    with pytest.raises(ConfigError, match="package_name"):
        load_config(_write(tmp_path, invalid))


def test_resolving_an_unconfigured_store_is_actionable(tmp_path: Path) -> None:
    application = load_config(_write(tmp_path, HUAWEI)).apps["wallet"]

    with pytest.raises(ConfigError) as raised:
        resolve_store_target(application, StoreName.HARMONYOS)

    assert raised.value.code == "STORE_NOT_CONFIGURED"
    assert "harmonyos" in str(raised.value)
