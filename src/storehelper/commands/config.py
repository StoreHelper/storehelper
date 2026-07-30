"""Configuration command helpers."""

from pathlib import Path

from storehelper.config.loader import load_config, write_example_config


def initialize(path: Path) -> None:
    write_example_config(path)


def validate(path: Path) -> None:
    load_config(path)
