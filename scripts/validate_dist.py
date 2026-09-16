"""Validate release archive boundaries and metadata before any upload."""

from __future__ import annotations

import argparse
import hashlib
import stat
import sys
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

PUBLIC_FILES = {".gitignore", "LICENSE", "README.md", "CHANGELOG.md", "pyproject.toml", "PKG-INFO"}
FORBIDDEN_DIRS = {
    "__pycache__",
    "cache",
    "secrets",
    "local-artifacts",
    "local_artifacts",
    "build",
    "dist",
}


def safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or "\\" in name
        or not path.parts
        or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts)
        or any(part in FORBIDDEN_DIRS for part in path.parts)
    ):
        raise ValueError(f"Unsafe archive path: {name}")
    return path


def validate_metadata(data: bytes, name: str, version: str) -> None:
    metadata = BytesParser().parsebytes(data)
    if metadata["Name"] != name or metadata["Version"] != version:
        raise ValueError("Distribution metadata does not match pyproject.toml")


def validate_dist(dist: Path) -> list[Path]:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    name, version = project["name"], project["version"]
    package = name.replace("-", "_")
    stem = f"{package}-{version}"
    expected = {f"{stem}-py3-none-any.whl", f"{stem}.tar.gz"}
    actual = {path.name for path in dist.iterdir() if path.name != "SHA256SUMS"}
    if actual != expected:
        raise ValueError(f"Expected exactly {sorted(expected)}, got {sorted(actual)}")
    wheel = dist / f"{stem}-py3-none-any.whl"
    info = f"{stem}.dist-info"
    wheel_metadata = {
        f"{info}/METADATA",
        f"{info}/WHEEL",
        f"{info}/RECORD",
        f"{info}/entry_points.txt",
        f"{info}/licenses/LICENSE",
    }
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate wheel members")
        for member in archive.infolist():
            path = safe_path(member.filename)
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("Wheel must not contain symbolic links")
            if member.filename not in wheel_metadata and not (
                path.parts[0] == package and path.suffix == ".py"
            ):
                raise ValueError(f"Unexpected wheel member: {member.filename}")
        if not {f"{package}/__init__.py", *wheel_metadata}.issubset(names):
            raise ValueError("Wheel lacks required package or metadata files")
        validate_metadata(archive.read(f"{info}/METADATA"), name, version)
    sdist = dist / f"{stem}.tar.gz"
    with tarfile.open(sdist) as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate sdist members")
        for member in members:
            # Hatchling includes the root VCS ignore file in sdists automatically.
            # Permit that one public file, not arbitrary dotfiles or directories.
            path = (
                PurePosixPath(member.name)
                if member.name == f"{stem}/.gitignore"
                else safe_path(member.name)
            )
            if not member.isfile():
                raise ValueError("Sdist must contain regular files only")
            relative = PurePosixPath(*path.parts[1:])
            if path.parts[0] != stem or not (
                str(relative) in PUBLIC_FILES
                or (relative.parts[:2] == ("src", package) and relative.suffix == ".py")
            ):
                raise ValueError(f"Unexpected sdist member: {member.name}")
        required = {
            f"{stem}/{file}" for file in ("LICENSE", "README.md", "pyproject.toml", "PKG-INFO")
        }
        required.add(f"{stem}/src/{package}/__init__.py")
        if not required.issubset(names):
            raise ValueError("Sdist lacks required source or metadata files")
        stream = archive.extractfile(f"{stem}/PKG-INFO")
        if stream is None:
            raise ValueError("Sdist metadata is unreadable")
        validate_metadata(stream.read(), name, version)
    return sorted([wheel, sdist])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--checksums", action="store_true")
    args = parser.parse_args()
    try:
        files = validate_dist(args.dist)
        if args.checksums:
            sums = "".join(
                f"{hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()}  {path.name}\n"
                for path in files
            )
            (args.dist / "SHA256SUMS").write_text(sums, encoding="utf-8")
    except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"Distribution validation failed: {error}", file=sys.stderr)
        return 1
    print("Validated wheel, sdist, package boundaries, and versions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
