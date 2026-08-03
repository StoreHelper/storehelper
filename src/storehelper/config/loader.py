"""Safe YAML loading, application selection, and example generation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from storehelper.config.models import ApplicationConfig, StoreHelperConfig
from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.models import StoreName, StoreTarget

_SECRET_KEYS = {
    "access_token",
    "authcode",
    "client_secret",
    "password",
    "private_key",
    "secret",
    "token",
}

_EXAMPLE = """version: 1

apps:
  my-app:
    package_name: com.example.app
    stores:
      huawei:
        app_id: "123456789"
        credential_profile: default
        language: zh-CN
      harmonyos:
        app_id: "987654321"
        package_name: com.example.app.harmony
        credential_profile: default
        language: zh-CN
      apple:
        app_id: "1234567890"
        bundle_id: com.example.app.ios
        app_store_version_id: 11111111-2222-3333-4444-555555555555
        credential_profile: apple-release
        platform: IOS
        language: en-US
      google_play:
        credential_profile: google-release
        track: internal
        release_status: draft
        language: en-US
      xiaomi:
        credential_profile: xiaomi-release
        app_name: Example App
        icon: assets/xiaomi-icon.png
        privacy_url: https://example.com/privacy
        language: zh-CN
"""


class ConfigError(StoreHelperError):
    """A safe, actionable configuration error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, ExitCode.USAGE)


def _normalized_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_")


def _find_secret_key(value: object, path: tuple[str, ...] = ()) -> tuple[str, ...] | None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = _normalized_key(raw_key)
            current = (*path, key)
            if key in _SECRET_KEYS:
                return current
            found = _find_secret_key(child, current)
            if found is not None:
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            found = _find_secret_key(child, (*path, str(index)))
            if found is not None:
                return found
    return None


def load_config(path: Path) -> StoreHelperConfig:
    """Load one explicitly selected schema version 1 YAML file."""

    if not path.is_file():
        raise ConfigError("CONFIG_NOT_FOUND", f"Configuration file not found: {path}")
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ConfigError("CONFIG_INVALID", f"Unable to read configuration: {error}") from None
    if not isinstance(raw, Mapping):
        raise ConfigError("CONFIG_INVALID", "Configuration root must be a mapping.")

    secret_path = _find_secret_key(raw)
    if secret_path is not None:
        dotted = ".".join(secret_path)
        raise ConfigError(
            "CONFIG_CONTAINS_SECRET",
            f"Secrets are not allowed in storehelper.yaml (field: {dotted}).",
        )
    try:
        return StoreHelperConfig.model_validate(
            raw,
            context={"config_dir": path.parent.resolve()},
        )
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors(include_input=False)
        )
        raise ConfigError("CONFIG_INVALID", f"Invalid configuration: {details}") from None


def select_application(
    config: StoreHelperConfig, alias: str | None
) -> tuple[str, ApplicationConfig]:
    """Select a named app, or the only app when the file is unambiguous."""

    if alias is not None:
        app = config.apps.get(alias)
        if app is None:
            raise ConfigError("APP_NOT_FOUND", f"Application is not configured: {alias}")
        return alias, app
    if len(config.apps) != 1:
        raise ConfigError(
            "APP_SELECTION_REQUIRED",
            "Multiple applications are configured; select one with --app.",
        )
    selected_alias = next(iter(config.apps))
    return selected_alias, config.apps[selected_alias]


def resolve_store_target(
    application: ApplicationConfig,
    store: StoreName,
) -> StoreTarget:
    """Resolve one configured store into the publisher's neutral target."""

    if store is StoreName.HUAWEI:
        huawei_config = application.stores.huawei
        if huawei_config is None:
            raise ConfigError(
                "STORE_NOT_CONFIGURED",
                f"Store is not configured for the selected application: {store.value}",
            )
        return StoreTarget(
            store=store,
            label="Huawei AppGallery (Android)",
            app_id=huawei_config.app_id,
            package_name=application.package_name,
            credential_profile=huawei_config.credential_profile,
            language=huawei_config.language,
        )

    if store is StoreName.HARMONYOS:
        harmony_config = application.stores.harmonyos
        if harmony_config is None:
            raise ConfigError(
                "STORE_NOT_CONFIGURED",
                f"Store is not configured for the selected application: {store.value}",
            )
        return StoreTarget(
            store=store,
            label="Huawei AppGallery (HarmonyOS)",
            app_id=harmony_config.app_id,
            package_name=harmony_config.package_name,
            credential_profile=harmony_config.credential_profile,
            language=harmony_config.language,
        )

    if store is StoreName.APPLE:
        apple_config = application.stores.apple
        if apple_config is None:
            raise ConfigError(
                "STORE_NOT_CONFIGURED",
                f"Store is not configured for the selected application: {store.value}",
            )
        return StoreTarget(
            store=store,
            label="Apple App Store",
            app_id=apple_config.app_id,
            package_name=apple_config.bundle_id,
            credential_profile=apple_config.credential_profile,
            language=apple_config.language,
            release_id=apple_config.app_store_version_id,
            platform=apple_config.platform,
        )

    if store is StoreName.GOOGLE_PLAY:
        google_config = application.stores.google_play
        if google_config is None:
            raise ConfigError(
                "STORE_NOT_CONFIGURED",
                f"Store is not configured for the selected application: {store.value}",
            )
        return StoreTarget(
            store=store,
            label=(f"Google Play ({google_config.track}, {google_config.release_status})"),
            app_id=application.package_name,
            package_name=application.package_name,
            credential_profile=google_config.credential_profile,
            language=google_config.language,
            track=google_config.track,
            release_status=google_config.release_status,
        )

    xiaomi_config = application.stores.xiaomi
    if xiaomi_config is None:
        raise ConfigError(
            "STORE_NOT_CONFIGURED",
            f"Store is not configured for the selected application: {store.value}",
        )
    return StoreTarget(
        store=store,
        label="Xiaomi App Store",
        app_id=application.package_name,
        package_name=application.package_name,
        credential_profile=xiaomi_config.credential_profile,
        language=xiaomi_config.language,
        app_name=xiaomi_config.app_name,
        icon_path=xiaomi_config.icon,
        privacy_url=xiaomi_config.privacy_url,
    )


def write_example_config(path: Path) -> None:
    """Create a secret-free example without overwriting user data."""

    if path.exists():
        raise ConfigError("CONFIG_EXISTS", f"Configuration file already exists: {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_EXAMPLE, encoding="utf-8")
    except OSError as error:
        raise ConfigError(
            "CONFIG_WRITE_FAILED", f"Unable to write configuration: {error}"
        ) from None
