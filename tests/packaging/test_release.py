"""Exercise actual builds and release guards, including untracked local files."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
PACKAGE = PROJECT["name"].replace("-", "_")
DIRTY_FILES = [
    ".agents/notes.md",
    ".codex/session.json",
    ".claude/secret.py",
    ".gitnexus/graph.json",
    ".env",
    "private-key.pem",
    "local-artifacts/build.apk",
    "cache/token.json",
    f"src/{PACKAGE}/.agents/leak.py",
    f"src/{PACKAGE}/__pycache__/leak.py",
    f"src/{PACKAGE}/secrets/leak.py",
    f"src/{PACKAGE}/local-artifacts/leak.py",
    f"src/{PACKAGE}/.env",
    f"src/{PACKAGE}/credentials.json",
]
MARKER = b"PACKAGING_TEST_LOCAL_SECRET_MUST_NOT_SHIP"


def run_script(name: str, *args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / name), *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )


@pytest.fixture(scope="module")
def built_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("dirty-source")
    for name in ("pyproject.toml", "README.md", "LICENSE", "CHANGELOG.md", ".gitignore"):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, root / name)
    shutil.copytree(ROOT / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
    for name in DIRTY_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(MARKER)
    completed = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--sdist", "--wheel"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return root


def test_dirty_working_files_cannot_enter_either_distribution(built_project: Path) -> None:
    wheel = next((built_project / "dist").glob("*.whl"))
    sdist = next((built_project / "dist").glob("*.tar.gz"))
    with zipfile.ZipFile(wheel) as archive:
        leaked = [name for name in archive.namelist() if MARKER in archive.read(name)]
    with tarfile.open(sdist) as archive:
        for member in archive.getmembers():
            if member.isfile():
                stream = archive.extractfile(member)
                assert stream is not None
                if MARKER in stream.read():
                    leaked.append(member.name)
    assert leaked == [], f"Local files entered distributions: {leaked}"


def test_clean_archives_pass_validation_and_generate_checksums(built_project: Path) -> None:
    result = run_script(
        "validate_dist.py", "--dist", str(built_project / "dist"), "--checksums", cwd=built_project
    )
    assert result.returncode == 0, result.stdout + result.stderr
    sums = (built_project / "dist" / "SHA256SUMS").read_text().splitlines()
    assert len(sums) == 2
    assert all(len(line.split()[0]) == 64 for line in sums)


@pytest.mark.parametrize(
    "injected",
    [
        f"{PACKAGE}/.env",
        f"{PACKAGE}/.agents/leak.py",
        "../escape.py",
    ],
)
def test_archive_validator_rejects_injected_files(
    built_project: Path,
    tmp_path: Path,
    injected: str,
) -> None:
    dist = tmp_path / "dist"
    shutil.copytree(built_project / "dist", dist)
    with zipfile.ZipFile(next(dist.glob("*.whl")), "a") as archive:
        archive.writestr(injected, MARKER)
    result = run_script("validate_dist.py", "--dist", str(dist), cwd=built_project)
    assert result.returncode == 1, result.stdout + result.stderr


def test_archive_validator_rejects_wrong_version(built_project: Path, tmp_path: Path) -> None:
    project = (built_project / "pyproject.toml").read_text()
    (tmp_path / "pyproject.toml").write_text(
        project.replace(f'version = "{PROJECT["version"]}"', 'version = "999.0.0"', 1)
    )
    result = run_script("validate_dist.py", "--dist", str(built_project / "dist"), cwd=tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr


def test_sdist_can_build_a_wheel_without_checkout(built_project: Path, tmp_path: Path) -> None:
    sdist = next((built_project / "dist").glob("*.tar.gz"))
    with tarfile.open(sdist) as archive:
        archive.extractall(tmp_path, filter="data")
    unpacked = next(tmp_path.iterdir())
    completed = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--wheel", str(unpacked)],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert len(list((unpacked / "dist").glob("*.whl"))) == 1


def test_release_guard_accepts_only_exact_project_tag() -> None:
    result = run_script("check_release.py", "--tag", f"v{PROJECT['version']}")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("tag", ["main", "v999.0.0", "refs/tags/v0.1.0", ""])
def test_release_guard_rejects_wrong_tag(tag: str) -> None:
    result = run_script("check_release.py", "--tag", tag)
    assert result.returncode == 1, result.stdout + result.stderr


def test_installed_smoke_rejects_wrong_installed_version() -> None:
    result = run_script("smoke_installed.py", "--version", "999.0.0")
    assert result.returncode == 1, result.stdout + result.stderr


def test_rehearsal_guard_requires_a_development_version(tmp_path: Path) -> None:
    result = run_script("check_release.py", "--testpypi-rehearsal")
    assert result.returncode == 1
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "0.1.0.dev1"\n'
    )
    result = run_script("check_release.py", "--testpypi-rehearsal", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
