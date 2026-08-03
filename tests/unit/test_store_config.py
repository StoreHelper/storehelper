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

APPLE = """      apple:
        app_id: '1234567890'
        bundle_id: com.example.wallet.ios
        app_store_version_id: version-resource-id
        credential_profile: apple-team
        platform: IOS
        language: zh-Hans
"""

GOOGLE_PLAY = """      google_play:
        credential_profile: google-release
        track: internal
        release_status: draft
        language: en-US
"""

XIAOMI = """      xiaomi:
        credential_profile: xiaomi-release
        app_name: Example Wallet
        icon: assets/xiaomi-icon.png
        privacy_url: https://example.com/privacy
        language: zh-CN
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
        "release_id": None,
        "platform": None,
        "track": None,
        "release_status": None,
        "app_name": None,
        "icon_path": None,
        "privacy_url": None,
    }


def test_dual_store_application_keeps_independent_package_names(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, HUAWEI + HARMONYOS))
    application = config.apps["wallet"]

    android = resolve_store_target(application, StoreName.HUAWEI)
    harmonyos = resolve_store_target(application, StoreName.HARMONYOS)

    assert android.package_name == "com.example.wallet"
    assert harmonyos.package_name == "com.example.wallet.harmony"
    assert android.credential_profile == harmonyos.credential_profile == "company"


def test_loads_apple_only_application_and_resolves_release_target(tmp_path: Path) -> None:
    application = load_config(_write(tmp_path, APPLE)).apps["wallet"]

    target = resolve_store_target(application, StoreName.APPLE)

    assert target.model_dump(mode="json") == {
        "store": "apple",
        "label": "Apple App Store",
        "app_id": "1234567890",
        "package_name": "com.example.wallet.ios",
        "credential_profile": "apple-team",
        "language": "zh-Hans",
        "release_id": "version-resource-id",
        "platform": "IOS",
        "track": None,
        "release_status": None,
        "app_name": None,
        "icon_path": None,
        "privacy_url": None,
    }


def test_three_store_application_keeps_independent_targets(tmp_path: Path) -> None:
    application = load_config(_write(tmp_path, HUAWEI + HARMONYOS + APPLE)).apps["wallet"]

    assert resolve_store_target(application, StoreName.HUAWEI).app_id == "100000001"
    assert resolve_store_target(application, StoreName.HARMONYOS).app_id == "100000002"
    assert resolve_store_target(application, StoreName.APPLE).release_id == "version-resource-id"


def test_loads_google_play_target_from_android_package_name(tmp_path: Path) -> None:
    application = load_config(_write(tmp_path, GOOGLE_PLAY)).apps["wallet"]

    target = resolve_store_target(application, StoreName.GOOGLE_PLAY)

    assert target.model_dump(mode="json") == {
        "store": "google_play",
        "label": "Google Play (internal, draft)",
        "app_id": "com.example.wallet",
        "package_name": "com.example.wallet",
        "credential_profile": "google-release",
        "language": "en-US",
        "release_id": None,
        "platform": None,
        "track": "internal",
        "release_status": "draft",
        "app_name": None,
        "icon_path": None,
        "privacy_url": None,
    }


def test_loads_xiaomi_update_target_and_resolves_relative_icon(tmp_path: Path) -> None:
    application = load_config(_write(tmp_path, XIAOMI)).apps["wallet"]

    target = resolve_store_target(application, StoreName.XIAOMI)

    assert target.model_dump(mode="json") == {
        "store": "xiaomi",
        "label": "Xiaomi App Store",
        "app_id": "com.example.wallet",
        "package_name": "com.example.wallet",
        "credential_profile": "xiaomi-release",
        "language": "zh-CN",
        "release_id": None,
        "platform": None,
        "track": None,
        "release_status": None,
        "app_name": "Example Wallet",
        "icon_path": str((tmp_path / "assets/xiaomi-icon.png").resolve()),
        "privacy_url": "https://example.com/privacy",
    }


@pytest.mark.parametrize(
    "invalid",
    [
        XIAOMI.replace("credential_profile: xiaomi-release", "credential_profile: ' '"),
        XIAOMI.replace("app_name: Example Wallet", "app_name: ' '"),
        XIAOMI.replace("icon: assets/xiaomi-icon.png", "icon: ' '"),
        XIAOMI.replace("https://example.com/privacy", "http://example.com/privacy"),
        XIAOMI.replace("https://example.com/privacy", "https://user:pass@example.com/privacy"),
        XIAOMI.replace("language: zh-CN", "language: zh_CN"),
        XIAOMI + "        unexpected: true\n",
    ],
)
def test_xiaomi_configuration_is_strict(tmp_path: Path, invalid: str) -> None:
    with pytest.raises(ConfigError, match="xiaomi"):
        load_config(_write(tmp_path, invalid))


def test_google_play_completed_release_is_explicit(tmp_path: Path) -> None:
    completed = GOOGLE_PLAY.replace("release_status: draft", "release_status: completed")
    application = load_config(_write(tmp_path, completed)).apps["wallet"]

    target = resolve_store_target(application, StoreName.GOOGLE_PLAY)

    assert target.release_status == "completed"


@pytest.mark.parametrize(
    "invalid",
    [
        GOOGLE_PLAY.replace("        release_status: draft\n", ""),
        GOOGLE_PLAY.replace("credential_profile: google-release", "credential_profile: ' '"),
        GOOGLE_PLAY.replace("track: internal", "track: production/../../secrets"),
        GOOGLE_PLAY.replace("release_status: draft", "release_status: staged"),
        GOOGLE_PLAY.replace("language: en-US", "language: en_US"),
        GOOGLE_PLAY + "        unexpected: true\n",
    ],
)
def test_google_play_configuration_is_strict(tmp_path: Path, invalid: str) -> None:
    with pytest.raises(ConfigError, match="google_play"):
        load_config(_write(tmp_path, invalid))


def test_google_play_coexists_with_existing_store_targets(tmp_path: Path) -> None:
    application = load_config(_write(tmp_path, HUAWEI + HARMONYOS + APPLE + GOOGLE_PLAY)).apps[
        "wallet"
    ]

    assert resolve_store_target(application, StoreName.HUAWEI).app_id == "100000001"
    assert resolve_store_target(application, StoreName.HARMONYOS).app_id == "100000002"
    assert resolve_store_target(application, StoreName.APPLE).app_id == "1234567890"
    assert resolve_store_target(application, StoreName.GOOGLE_PLAY).track == "internal"


@pytest.mark.parametrize(
    "invalid",
    [
        APPLE.replace("com.example.wallet.ios", "not-a-bundle"),
        APPLE.replace("version-resource-id", " "),
        APPLE.replace("platform: IOS", "platform: MAC_OS"),
        APPLE + "        unexpected: true\n",
    ],
)
def test_apple_configuration_is_strict(tmp_path: Path, invalid: str) -> None:
    with pytest.raises(ConfigError, match="apple"):
        load_config(_write(tmp_path, invalid))


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
