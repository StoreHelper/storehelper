# Changelog

## 0.9.0 — release candidate

- Generate a one-store, secret-free `storehelper init` configuration by default; select a market
  with `--store` and optionally validate an artifact with `--file`.
- Derive Android, HarmonyOS and Apple package identity, plus Android/HarmonyOS version codes,
  from bounded local artifact metadata. Explicit YAML values remain supported as equality checks.
- Accept minimal YAML for publishing while rejecting identity mismatches before credential or
  network access. Read-only verification/status accept `--file` or a unique saved run; resume
  binds the saved artifact SHA-256 and target identity before credentials.

## 0.8.1 — 2026-09-16

- Opt-in `STOREHELPER_PROJECT_ROOT` confines release inputs and run records to one project.
  Scoped history uses `.storehelper/runs`, without global fallback; ordinary CLI behavior is unchanged.
- Check configuration, artifacts, notes, Xiaomi icons and receipt paths, including symlink targets.
- Explicit wheel/sdist allowlists and regression tests prevent local development files from shipping.
- Release gates cover tests, 90% coverage, lint, typing, archive validation and installed-wheel smoke.
- Separate TestPyPI rehearsals and OIDC promotion of identical checksummed artifacts to PyPI/GitHub.

Published on PyPI and GitHub as `v0.8.1`. Eight-store adapters from 0.8.0 are retained.
Live vendor acceptance remains a separate maintainer check.
