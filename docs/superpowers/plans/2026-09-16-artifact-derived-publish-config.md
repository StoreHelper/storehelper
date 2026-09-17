# Artifact-derived publishing configuration implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow minimal, store-specific YAML while deriving package identity and version code from the selected artifact before publication.

**Architecture:** Keep structural package validators compatible with existing sparse fixtures. Add bounded, opt-in metadata inspectors; resolve a complete `StoreTarget` from artifact metadata plus required market configuration; retain configured identities as assertions and legacy fallback. CLI commands that lack an artifact accept `--file` or an unambiguous saved run.

**Tech Stack:** Python 3.11+, Typer, Pydantic v2, pytest, stdlib ZIP/plist/JSON, apkInspector for APK binary XML, protobuf for AAB manifest.

**Spec:** `docs/superpowers/specs/2026-09-16-artifact-derived-publish-config-design.md`

## Global Constraints

- Preserve `version: 1` YAML and all existing complete configurations.
- Never place credentials in YAML or pass secrets in command arguments.
- Inspect bounded metadata only; never execute or extract package contents to disk.
- Reject an artifact/config identity mismatch before credential resolution or network access.
- Preserve `resume` package SHA-256 and saved target identity checks.
- Python 3.11, 3.12, 3.13, and 3.14 remain supported.

---

### Task 1: Android metadata reader

**Files:** Create `src/storehelper/artifacts/identity.py` and `src/storehelper/artifacts/android_metadata.py`; modify `pyproject.toml` and `uv.lock`; test `tests/unit/test_android_metadata.py`.

**Interfaces:** Define immutable `ArtifactIdentity(package_name: str, version_code: int | None, version_name: str | None)` in `artifacts/identity.py`; produce `inspect_android_metadata(path: Path) -> ArtifactIdentity | None`. Return `None` only for legacy sparse/unrecognized manifests; raise a package-validation error for a recognizable malformed or oversized manifest.

- [ ] Write small APK and AAB fixtures and tests asserting literal package name and `versionCode`, plus duplicate/oversized/malformed manifest failures.
- [ ] Run `pytest tests/unit/test_android_metadata.py -q` and confirm the expected missing API failure.
- [ ] Implement bounded ZIP-member reads; APK compiled XML via `apkInspector.axml.get_manifest()` on bounded bytes, AAB `aapt.pb.XmlNode` root attributes via a minimal protobuf descriptor; validate root names and namespaces.
- [ ] Re-run focused tests, Ruff, and mypy; keep `validate_package()`'s existing sparse-fixture behavior unchanged.

### Task 2: HarmonyOS metadata reader

**Files:** Create `src/storehelper/artifacts/harmony_metadata.py`; test `tests/unit/test_harmony_metadata.py`.

**Interfaces:** Produce `inspect_harmony_metadata(path: Path) -> ArtifactIdentity | None` with the same model. APP reads exact root `pack.info` summary; HAP reads exact root `module.json` app object.

- [ ] Write APP/HAP ZIP tests for `bundleName` and version code, and negative tests for duplicate members, oversized JSON, malformed types, nested decoys, and sparse legacy archives.
- [ ] Run `pytest tests/unit/test_harmony_metadata.py -q` and confirm the expected missing API failure.
- [ ] Implement a bounded JSON member reader with strict UTF-8, unique JSON keys, and no nested-HAP extraction; distinguish unavailable metadata from corrupt metadata.
- [ ] Re-run focused tests, Ruff, and mypy; leave structural validator behavior unchanged.

### Task 3: Optional YAML fields and target binding

**Files:** Modify `src/storehelper/config/models.py`, `src/storehelper/config/loader.py`, `src/storehelper/artifacts/models.py`, `src/storehelper/artifacts/identity.py`; test `tests/unit/test_config.py`, `tests/unit/test_store_config.py`, `tests/unit/test_artifact_target_resolution.py`.

**Interfaces:** `resolve_store_target(application, store, identity: ArtifactIdentity | None = None) -> StoreTarget` produces a fully populated target or a field-specific `ConfigError`. The inspector dispatch `inspect_artifact_identity(path, store) -> ArtifactIdentity | None` converts the existing Apple IPA metadata and the new Android/Harmony metadata to one shape.

- [ ] Test that minimal YAML loads; old YAML still resolves; artifact data fills missing fields; explicit mismatch fails; missing both sources names the field; Apple-only config needs no dummy Android package.
- [ ] Run the focused tests and confirm schema/target failures.
- [ ] Make only package/bundle/version-code YAML fields optional; keep provided-value validation. Bind target fields with one mismatch-check helper and use store-specific artifact inspection.
- [ ] Run focused tests, Ruff, and mypy.

### Task 4: Publish, resume, and read-only CLI paths

**Files:** Modify `src/storehelper/cli.py`, `src/storehelper/runtime.py`, `src/storehelper/publishing/service.py`, `src/storehelper/runs/models.py`, and only necessary OPPO/vivo/HONOR read-only adapter guards; test `tests/integration/test_cli_publish.py`, `tests/integration/test_artifact_derived_cli.py`, `tests/unit/test_publishing_service.py`.

**Interfaces:** Publish validates/inspects `--file` before credentials, resolves the target once, and passes the prevalidated artifact into Publisher. `status`/`credentials verify` accept optional `--file`; when absent they use complete YAML or an unambiguous matching receipt. Resume uses the saved path and SHA-256, inspects it, then resolves against receipt and YAML.

- [ ] Test minimal-config dry run, publish target binding, mismatch before credentials/network, read-only `--file`, conflicting receipt rejection, and resumable identity consistency.
- [ ] Run the focused tests and confirm missing-target/CLI failures.
- [ ] Thread the resolved target through runtime, reuse the prevalidated artifact, and add explicit read-only artifact/receipt resolution; keep old call signatures working via optional arguments.
- [ ] Run focused integration/unit tests, Ruff, and mypy.

### Task 5: Minimal `init` and public guidance

**Files:** Modify `src/storehelper/config/loader.py`, `src/storehelper/cli.py`, `examples/storehelper.yaml`, `README.md`, `CHANGELOG.md`; test `tests/integration/test_cli_foundation.py`, `tests/unit/test_config.py`.

**Interfaces:** `storehelper init` defaults to a one-store Huawei template; `--store` selects one market; optional `--file` validates that a package is inspectable for that market but does not persist derived identity.

- [ ] Test one-store output, each store selector, artifact mismatch/type rejection, no overwrite, and `config validate` success.
- [ ] Run focused tests and confirm old template behavior fails those new expectations.
- [ ] Implement store-specific secret-free templates and update CLI/help/examples/README with explicit fields that remain required.
- [ ] Run focused tests and verify no key/secret value appears in generated YAML.

### Task 6: Final compatibility and packaging verification

**Files:** Any targeted fixes needed by test evidence only.

- [ ] Create/sync an isolated editable environment in the worktree so `importlib.metadata.version("storehelper")` reports `0.8.1` rather than the pre-existing shared environment's `0.8.0`.
- [ ] Run the full pytest suite, `ruff check .`, `mypy src`, package build, strict Twine check on wheel/sdist only, and `scripts/validate_dist.py --checksums`.
- [ ] Review the complete diff for credential leakage, accidental changes to `.mimosa/`, and backwards-incompatible YAML or CLI behavior; document any remaining format limitations.
