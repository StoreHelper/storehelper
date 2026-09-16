"""Offline smoke check; run with the Python from a fresh wheel-only virtualenv."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def check_install(name: str, expected_version: str) -> None:
    require(importlib.metadata.version(name) == expected_version, "Installed version mismatch")
    module = importlib.import_module(name.replace("-", "_"))
    require(module.__file__ is not None, "Package has no import location")
    require(
        Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()),
        "Package was imported from the checkout, not the isolated virtualenv",
    )
    require(module.__version__ == expected_version, "Runtime version mismatch")


def clean_environment(root: Path) -> dict[str, str]:
    # No credentials, user config, PYTHONPATH, or editable checkout leaks.
    return {
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath,
        "HOME": str(root),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "PYTHONNOUSERSITE": "1",
        "STOREHELPER_MCP_ROOT": str(root),
        "STOREHELPER_MCP_ALLOW_MUTATIONS": "0",
    }


def run_cli(root: Path, *args: str) -> str:
    command = Path(sys.executable).parent / (
        "storehelper.exe" if os.name == "nt" else "storehelper"
    )
    completed = subprocess.run(
        [str(command), *args],
        cwd=root,
        env=clean_environment(root),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout


def check_config(root: Path) -> None:
    require(bool(run_cli(root, "--help")), "CLI help is empty")
    created = json.loads(run_cli(root, "init", "--config", "storehelper.yaml", "--output", "json"))
    require(created["ok"] is True and (root / "storehelper.yaml").is_file(), "Config not created")
    validated = json.loads(run_cli(root, "config", "validate", "--output", "json"))
    require(validated == {"ok": True, "valid": True}, "Offline config validation failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        check_install("storehelper", args.version)
        with tempfile.TemporaryDirectory(prefix="storehelper-smoke-") as directory:
            root = Path(directory).resolve()
            require(run_cli(root, "version").strip() == args.version, "Console version mismatch")
            check_config(root)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Installed-wheel smoke failed: {error}", file=sys.stderr)
        return 1
    print("Installed wheel: version, help, config creation and offline validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
