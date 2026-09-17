"""Configuration command helpers."""

from pathlib import Path

from storehelper.artifacts.inspect import inspect_artifact_identity
from storehelper.config.loader import ConfigError, load_config, write_example_config
from storehelper.project import project_path
from storehelper.stores.models import StoreName
from storehelper.stores.registry import get_registration


def initialize(
    path: Path,
    *,
    store: StoreName = StoreName.HUAWEI,
    file: Path | None = None,
) -> None:
    if path.exists():
        raise ConfigError("CONFIG_EXISTS", f"Configuration file already exists: {path}")
    if file is not None:
        selected = project_path(file, "artifact")
        artifact = get_registration(store).validator(selected)
        identity = inspect_artifact_identity(selected, store, artifact)
        if identity is None:
            raise ConfigError(
                "CONFIG_ARTIFACT_IDENTITY_UNAVAILABLE",
                "Package identity is unreadable; omit --file or use a valid artifact.",
            )
    write_example_config(path, store=store)


def validate(path: Path) -> None:
    load_config(path)
