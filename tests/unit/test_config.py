from pathlib import Path

import pytest

from storehelper.config.loader import (
    ConfigError,
    load_config,
    select_application,
    write_example_config,
)
from storehelper.stores.models import StoreName


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "storehelper.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def valid_config(*, extra_huawei: str = "") -> str:
    return f"""version: 1
apps:
  wallet:
    package_name: com.example.wallet
    stores:
      huawei:
        app_id: '123456789'
        credential_profile: company
{extra_huawei}"""


def test_loads_one_huawei_application_with_default_language(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, valid_config()))

    alias, app = select_application(config, None)

    assert alias == "wallet"
    assert app.package_name == "com.example.wallet"
    assert app.stores.huawei.app_id == "123456789"
    assert app.stores.huawei.language == "zh-CN"


@pytest.mark.parametrize(
    "secret_key",
    [
        "private_key",
        "client_secret",
        "access_key",
        "secret_key",
        "access_token",
        "password",
        "authCode",
    ],
)
def test_rejects_secret_fields_anywhere(tmp_path: Path, secret_key: str) -> None:
    path = write_config(tmp_path, valid_config(extra_huawei=f"        {secret_key}: leaked\n"))

    with pytest.raises(ConfigError, match="Secrets are not allowed") as raised:
        load_config(path)

    assert "leaked" not in str(raised.value)


def test_rejects_unknown_fields(tmp_path: Path) -> None:
    path = write_config(tmp_path, valid_config(extra_huawei="        appid: wrong-spelling\n"))

    with pytest.raises(ConfigError, match="appid"):
        load_config(path)


def test_multiple_apps_require_explicit_selection(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        valid_config()
        + """  merchant:
    package_name: com.example.merchant
    stores:
      huawei:
        app_id: '987654321'
        credential_profile: company
""",
    )
    config = load_config(path)

    with pytest.raises(ConfigError) as raised:
        select_application(config, None)

    assert raised.value.code == "APP_SELECTION_REQUIRED"


def test_selects_named_application(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, valid_config()))

    alias, app = select_application(config, "wallet")

    assert alias == "wallet"
    assert app.package_name == "com.example.wallet"


def test_example_config_never_overwrites_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "storehelper.yaml"
    path.write_text("keep-me", encoding="utf-8")

    with pytest.raises(ConfigError) as raised:
        write_example_config(path)

    assert raised.value.code == "CONFIG_EXISTS"
    assert path.read_text(encoding="utf-8") == "keep-me"


def test_example_config_is_valid_and_secret_free(tmp_path: Path) -> None:
    path = tmp_path / "storehelper.yaml"

    write_example_config(path)
    config = load_config(path)

    assert list(config.apps) == ["my-app"]
    contents = path.read_text(encoding="utf-8")
    assert "private_key" not in contents
    assert "client_secret" not in contents
    assert config.apps["my-app"].package_name is None
    assert config.apps["my-app"].stores.huawei is not None
    assert config.apps["my-app"].stores.apple is None
    assert "package_name" not in contents


@pytest.mark.parametrize("store", list(StoreName))
def test_example_config_selects_exactly_one_store(tmp_path: Path, store: StoreName) -> None:
    path = tmp_path / "storehelper.yaml"
    write_example_config(path, store=store)
    config = load_config(path)
    selected = config.apps["my-app"].stores
    assert getattr(selected, store.value) is not None
    assert sum(getattr(selected, name.value) is not None for name in StoreName) == 1
