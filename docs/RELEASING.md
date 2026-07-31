# Releasing StoreHelper

## One-time repository setup

1. Create the `storehelper` project on PyPI without uploading a package manually.
2. Add a PyPI Trusted Publisher for `StoreHelper/storehelper`, workflow
   `.github/workflows/release.yml`, environment `pypi`.
3. Create a protected GitHub environment named `pypi`; require maintainer approval if desired.
4. Protect `main`, require the CI workflow, and require pull-request review.

No PyPI token is stored in GitHub. The release workflow uses GitHub OIDC.

## Release checklist

1. Update `version` in `pyproject.toml` and the changelog/release notes.
2. Run the complete local verification documented in `README.md`.
3. Merge the release pull request to `main`.
4. Create and push an immutable signed tag such as `v0.1.0`.
5. Approve the protected `pypi` environment deployment.
6. Verify the PyPI wheel/sdist, GitHub Release artifacts, and `SHA256SUMS`.
7. Install from PyPI with `pipx install storehelper` and run `storehelper version`.

The workflow builds once, verifies the artifacts, then uses the same downloaded artifacts for
PyPI and GitHub Releases. Never reuse or move an existing version tag.

## Homebrew

The initial supported installation channel is `pipx`. After the first stable PyPI release,
create the separate `StoreHelper/homebrew-tap` repository and a formula that installs the PyPI
artifact and verifies its checksum. Do not put the tap formula in this repository.
