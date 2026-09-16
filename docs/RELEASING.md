# Releasing StoreHelper

Source preparation is not publication. Only an authorized maintainer should run external releases.

## One-time setup

1. Confirm public repository ownership; protect `main`, version tags and pull-request review.
2. Configure [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/). For a new project,
   use a pending publisher: project `storehelper`, owner `StoreHelper`, repository `storehelper`,
   workflow filename **`release.yml`**, environment **`pypi`**. Use the filename, not the full path.
   A pending publisher does not reserve a name.
3. Configure TestPyPI separately with environment `testpypi`. Both `release.yml` (tag staging) and
   `testpypi.yml` (manual rehearsal) need publisher entries. To bootstrap through rehearsal, register
   the pending `testpypi.yml` identity first, then add `release.yml` once the TestPyPI project exists.
4. Create GitHub `testpypi` and `pypi` environments with required reviewers, prevent self-review where
   available, and restrict allowed release refs. Allow reviewed rehearsal branches on TestPyPI only.
   Merely naming environments in YAML does not configure their protection.

No long-lived PyPI token or app-store credential is required in Actions; uploads use GitHub OIDC.

## Local preflight

In a Python 3.11+ environment with `pip install -e '.[dev]'`:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy src
python -m pytest --cov=storehelper --cov-fail-under=90
python scripts/check_release.py --tag v0.8.1
python -m build
python -m twine check --strict dist/*.whl dist/*.tar.gz
python scripts/validate_dist.py --checksums
```

Use fresh build output: validation rejects extra stale files in `dist`. CI additionally installs
the wheel in a fresh environment and runs `scripts/smoke_installed.py` outside the checkout,
checking version/import location and offline CLI behavior. Do not upload `SHA256SUMS` to PyPI.

## TestPyPI rehearsal

On a reviewed temporary branch, choose a unique development version, for example `0.8.1.dev1`.
Update `pyproject.toml`, the source fallback version, version assertions and `uv.lock`. Run the
**TestPyPI rehearsal** workflow (`testpypi.yml`) and approve `testpypi`. Non-development versions
are rejected so a rehearsal cannot consume the intended release version. This does not publish
to production PyPI. Do not merge the rehearsal version bump or reuse uploaded versions.

Install the exact TestPyPI wheel in an isolated environment, resolving dependencies from normal
PyPI. Avoid unconstrained `--extra-index-url`: the same package name can exist on both indexes.

## Production release

1. Review/merge the `0.8.1` candidate to `main` and wait for green CI.
2. When authorized, create/push immutable tag `v0.8.1` for that reviewed commit.
3. The workflow checks tag/version; tests Python 3.11–3.14; runs formatting, lint, typing and 90%
   coverage gates; builds once (wheel from sdist); validates archives and installed-wheel smoke.
4. Approve TestPyPI staging, review the result, then approve PyPI promotion. Both receive identical
   checksummed artifacts, not separately rebuilt packages.
5. Only after PyPI succeeds, GitHub Release receives the wheel, sdist and `SHA256SUMS`.
6. Verify an independent `pipx install "storehelper==0.8.1"`, version/help/config and vendor dry-runs.
7. Publish MCP **after** CLI 0.8.1 is installable on public PyPI.

After an upload, rerun only failed downstream jobs. Never move tags or overwrite published versions;
code fixes require a new version. Package upload success is not live vendor acceptance.

## Homebrew

Initial installation is PyPI via pipx/uv. A separate `StoreHelper/homebrew-tap` can follow a verified
Python release, with tested formulae and pinned checksums. No tap is created by this change.
