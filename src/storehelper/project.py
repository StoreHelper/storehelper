"""Opt-in project confinement for automation hosts (not an OS sandbox)."""

from __future__ import annotations

import os
from pathlib import Path

from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode


class ProjectError(StoreHelperError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, ExitCode.USAGE)


def project_root() -> Path | None:
    """Absence preserves the legacy CLI; an invalid configured root fails closed."""
    value = os.environ.get("STOREHELPER_PROJECT_ROOT")
    if value is None:
        return None
    try:
        candidate = Path(value)
        if not value.strip() or not candidate.is_absolute():
            raise ValueError
        root = candidate.resolve(strict=True)
        if not root.is_dir():
            raise ValueError
        return root
    except (OSError, RuntimeError, ValueError):
        raise ProjectError(
            "PROJECT_ROOT_INVALID",
            "STOREHELPER_PROJECT_ROOT must be an absolute path to an existing directory.",
        ) from None


def confine_path(path: Path, label: str, *, root: Path) -> Path:
    """Resolve existing symlinks, including parents of not-yet-created files."""
    try:
        candidate = path if path.is_absolute() else root / path
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
        return resolved
    except (OSError, RuntimeError, ValueError):
        raise ProjectError(
            "PROJECT_PATH_OUTSIDE_ROOT",
            f"The {label} path must remain inside the configured project root.",
        ) from None


def project_path(path: Path, label: str) -> Path:
    root = project_root()
    return path if root is None else confine_path(path, label, root=root)
