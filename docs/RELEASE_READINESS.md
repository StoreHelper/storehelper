# Release readiness / 发布准备

## Current CLI candidate: 0.9.0

CLI `0.8.1` was published to PyPI and GitHub on 2026-09-16. The `0.9.0` candidate adds
artifact-derived configuration on top of the same eight app-store adapters. Source preparation,
a green local test suite, and a GitHub push do **not** publish a new package.

- [x] Implement bounded APK/AAB, APP/HAP and IPA identity inspection with mismatch checks.
- [x] Preserve existing YAML fields as optional equality checks where package metadata is available.
- [x] Resolve read-only verification/status by explicit artifact or an unambiguous saved run.
- [x] Bind resume to the saved artifact hash and resolved target before credential/network access.
- [x] Complete local release gates on the final versioned candidate: Python tests with at least
  90% coverage, Ruff, mypy, tag/version guard, wheel/sdist, Twine and archive validation.
- [ ] Push a PR to `main`; wait for required Python 3.11–3.14 and build checks, then merge.
- [ ] Create immutable tag `v0.9.0` from the reviewed `main` commit only after release approval.
- [ ] Approve the TestPyPI and PyPI environments; verify the GitHub Release and public PyPI files.
- [ ] After publication, independently install public `storehelper==0.9.0` with `pipx` and run
  offline CLI/package smoke tests.
- [ ] Run real-vendor acceptance with dedicated test applications; mocks alone do not prove vendor
  approval or availability.

The release workflow builds one distribution set and promotes the identical checksummed artifacts
through TestPyPI, PyPI and GitHub Release. Never move a published tag or reuse an uploaded version;
release fixes require a new version. See [RELEASING.md](RELEASING.md) for commands and approval
steps.

Local candidate evidence (macOS, Python 3.12.6): 1052 tests passed with 91.02% coverage;
Ruff formatting/lint, mypy, lock and tag checks passed. A wheel built from the sdist with
`build --no-isolation` passed strict Twine, archive/checksum and an independently installed-wheel
smoke test. Local isolated build dependency installation could not verify the PyPI TLS certificate;
the required GitHub CI build remains the independent isolated-build gate.

## MCP and remaining product scope

MCP, Flutter and IDE plugins are separate repositories or future deliverables. The CLI release does
not automatically publish MCP or register it in any MCP marketplace. MCP requires a published CLI
version of at least `0.8.1`; verify compatibility before changing its dependency floor. Store
credentials remain in the OS keyring or CI secret storage, never in `storehelper.yaml` or the
distribution archives.
