# Changelog

## 0.8.1 — release preparation

- Opt-in `STOREHELPER_PROJECT_ROOT` confines release inputs and run records to one project.
  Scoped history uses `.storehelper/runs`, without global fallback; ordinary CLI behavior is unchanged.
- Check configuration, artifacts, notes, Xiaomi icons and receipt paths, including symlink targets.
- Explicit wheel/sdist allowlists and regression tests prevent local development files from shipping.
- Release gates cover tests, 90% coverage, lint, typing, archive validation and installed-wheel smoke.
- Separate TestPyPI rehearsals and OIDC promotion of identical checksummed artifacts to PyPI/GitHub.

Prepared changes, not an already-published release. Eight-store adapters from 0.8.0 are retained.
Live vendor acceptance remains a separate maintainer check.
