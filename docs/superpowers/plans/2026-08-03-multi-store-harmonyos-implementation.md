# Multi-Store Foundation and HarmonyOS v0.2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Use test-driven development for every production behavior and verification-before-completion before claiming a task or milestone complete.

**Goal:** Generalize StoreHelper's Huawei-only core into a secure, resumable multi-store publisher and add end-to-end `.app`/`.hap` publishing for existing HarmonyOS applications in AppGallery Connect.

**Architecture:** A store-neutral `Publisher` consumes a resolved `StoreRuntime`. A static registry maps a canonical store name to capabilities, artifact validation, credential kind, and adapter construction. Huawei Android and HarmonyOS share Service Account credentials while retaining independent package and HTTP protocols. Local receipt schema v2 stores a generic `artifact_id` and migrates v1 receipts on read.

**Tech stack:** Python 3.11+, Typer, httpx, Pydantic, PyYAML, cryptography, keyring, platformdirs, pytest, pytest-asyncio, respx, Ruff, mypy, Hatchling.

## Global constraints

- Preserve existing Huawei Android behavior and schema version 1 configuration files.
- Support only built-in `huawei` and `harmonyos` stores in this increment.
- Support only existing AppGallery Connect applications and Huawei Service Account authentication.
- Accept `.app` and `.hap` HarmonyOS artifacts up to 4 GiB.
- Update only the configured language's `newFeatures` field.
- Never persist or emit a private key, JWT, bearer token, signed OBS header, OBS URL, object ID, or raw vendor payload.
- Never load an entire app package into memory.
- Never send Huawei API authorization headers to the temporary OBS origin.
- Never call a live vendor API from automated tests.
- Introduce each production behavior with a focused failing test.
- Keep `docs/DEVELOPMENT_PLAN.md` updated only after the relevant verification passes.
- Keep statement coverage at or above 90%.

## File map

```text
src/storehelper/artifacts/models.py              generic local artifact value model
src/storehelper/stores/models.py                 shared store, capability, target, state models
src/storehelper/stores/errors.py                 shared vendor/retry errors
src/storehelper/stores/base.py                   store-neutral adapter protocol
src/storehelper/stores/registry.py               immutable built-in adapter registry
src/storehelper/runtime.py                       config + credentials + adapter construction
src/storehelper/config/models.py                 Huawei/HarmonyOS strict configuration
src/storehelper/runs/models.py                    v2 receipt and v1 migration input
src/storehelper/runs/repository.py                migration-aware atomic storage
src/storehelper/publishing/service.py             store-neutral publishing state machine
src/storehelper/stores/huawei/*.py                adapted Android implementation
src/storehelper/stores/harmonyos/package.py       APP/HAP validation
src/storehelper/stores/harmonyos/client.py        sanitized v2/v3 and OBS HTTP boundary
src/storehelper/stores/harmonyos/adapter.py       HarmonyOS publishing operations
src/storehelper/cli.py                            registry-based commands and messages
tests/unit/                                       pure model, parser, state, security tests
tests/integration/                                mocked HTTP and CLI contract tests
README.md                                         public usage and security guide
examples/storehelper.yaml                         redacted dual-store configuration
docs/HARMONYOS_MANUAL_TEST.md                     opt-in live verification checklist
docs/DEVELOPMENT_PLAN.md                          overall rollout and completion tracker
```

---

### Task 1: Store-neutral models, errors, and Huawei compatibility

**Files:**
- Create: `src/storehelper/artifacts/__init__.py`
- Create: `src/storehelper/artifacts/models.py`
- Create: `src/storehelper/stores/models.py`
- Create: `src/storehelper/stores/errors.py`
- Modify: `src/storehelper/stores/base.py`
- Modify: `src/storehelper/stores/huawei/models.py`
- Modify: `src/storehelper/stores/huawei/package.py`
- Modify: `src/storehelper/stores/huawei/adapter.py`
- Modify: `src/storehelper/stores/huawei/errors.py`
- Create: `tests/unit/test_store_models.py`
- Modify: `tests/unit/test_huawei_package.py`
- Modify: `tests/unit/test_huawei_compile.py`

**Interfaces:**
- `StoreName`, `CredentialKind`, `StoreCapabilities`, `StoreTarget`.
- `ArtifactInfo`, `VerifiedApplication`, `UploadedArtifact`.
- `ProcessingState`, `ProcessingStatus`, `ReviewStatus`.
- `StoreVendorError` and `ArtifactStillProcessingError`.
- A Huawei adapter conforming to the new generic protocol without changed HTTP behavior.

- [ ] Write failing tests for enum serialization, frozen capability models, generic artifact values, and safe errors.
- [ ] Run `python -m pytest tests/unit/test_store_models.py -q` and verify missing imports fail.
- [ ] Implement the shared models and protocol; move or alias Huawei types to them.
- [ ] Rename adapter method arguments internally from `pkg_version` to `artifact_id` while preserving Huawei request field names.
- [ ] Make `HuaweiVendorError` derive from `StoreVendorError`; translate package-compiling responses into `ArtifactStillProcessingError`.
- [ ] Run `python -m pytest tests/unit/test_store_models.py tests/unit/test_huawei_package.py tests/unit/test_huawei_compile.py tests/integration/test_huawei_submission.py -q`.
- [ ] Run `ruff format . && ruff check . && mypy src`.
- [ ] Commit: `refactor: introduce store-neutral publishing contracts`.

### Task 2: Strict multi-store configuration and resolved targets

**Files:**
- Modify: `src/storehelper/config/models.py`
- Modify: `src/storehelper/config/loader.py`
- Modify: `src/storehelper/commands/config.py`
- Create: `tests/unit/test_store_config.py`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/integration/test_cli_foundation.py`

**Interfaces:**
- `HarmonyOSStoreConfig(app_id, package_name, credential_profile, language)`.
- Optional `huawei` and `harmonyos` entries with at least one configured store.
- `resolve_store_target(application, store) -> StoreTarget`.

- [ ] Add failing tests for Huawei-only backward compatibility, HarmonyOS-only and dual-store applications, at-least-one-store validation, unknown fields, invalid bundle names, and unconfigured store selection.
- [ ] Run `python -m pytest tests/unit/test_store_config.py tests/unit/test_config.py -q` and verify failures.
- [ ] Implement strict optional store fields plus a model-level at-least-one validator.
- [ ] Keep the application-level Huawei package name unchanged; require a HarmonyOS store-local package name.
- [ ] Implement target resolution with bounded `STORE_NOT_CONFIGURED` errors.
- [ ] Update example generation without embedding secret placeholders.
- [ ] Run focused tests, then `python -m pytest tests/integration/test_cli_foundation.py -q`.
- [ ] Commit: `feat: add strict HarmonyOS project configuration`.

### Task 3: Receipt schema v2 and safe migration

**Files:**
- Modify: `src/storehelper/runs/models.py`
- Modify: `src/storehelper/runs/repository.py`
- Modify: `src/storehelper/domain/models.py`
- Modify: `tests/unit/test_run_repository.py`
- Modify: `tests/unit/test_domain.py`
- Modify: `tests/unit/test_renderers.py`

**Interfaces:**
- New receipts serialize `schema_version: 2`, actual `store`, and `artifact_id`.
- Version 1 input migrates `pkg_version` to `artifact_id`.
- `OperationResult` and `PublishRequest` accept registered store names while operation JSON remains schema version 1.

- [ ] Add failing tests that load a literal v1 JSON receipt, expose `artifact_id`, and rewrite v2 atomically.
- [ ] Add failing tests for HarmonyOS operation-result JSON, success/failure factories, and next-action output.
- [ ] Run the focused tests and verify model validation failures.
- [ ] Implement an explicit v1 migration function rather than accepting arbitrary aliases silently.
- [ ] Ensure migration rejects future versions, invalid store names, missing durable fields, and secret-like extra data.
- [ ] Change duplicate detection to `(store, app_id, artifact_sha256)` and repository creation to require `store`.
- [ ] Run `python -m pytest tests/unit/test_run_repository.py tests/unit/test_domain.py tests/unit/test_renderers.py -q`.
- [ ] Commit: `feat: migrate publishing receipts to generic schema v2`.

### Task 4: Store-neutral publishing state machine

**Files:**
- Modify: `src/storehelper/publishing/service.py`
- Modify: `tests/unit/test_publishing_service.py`
- Modify: `tests/integration/test_huawei_submission.py`

**Interfaces:**
- `Publisher(adapter, repository, target, validator, capabilities, store_label, ...)`.
- No import from `stores.huawei` or `stores.harmonyos` in the publisher.
- Capability-driven processing poll and a generic eventual-consistency retry.

- [ ] Replace test fakes with store-neutral artifacts, upload results, processing statuses, and targets.
- [ ] Add failing parameterized tests for both store names, polling enabled/disabled, release-notes requirements, duplicate detection, interruption, timeout, digest changes, and resumption.
- [ ] Run `python -m pytest tests/unit/test_publishing_service.py -q` and verify failures.
- [ ] Refactor publish, resume, wait, status, and messages to use target/capabilities and `artifact_id`.
- [ ] Catch only `ArtifactStillProcessingError` for the submit-to-poll transition.
- [ ] Keep serialized `package_compiling` stage/state values for compatibility.
- [ ] Run focused tests and existing Huawei integration tests.
- [ ] Prove the publisher contains no vendor import with `rg -n "stores\.(huawei|harmonyos)|Huawei" src/storehelper/publishing`.
- [ ] Commit: `refactor: generalize resumable publishing orchestration`.

### Task 5: HarmonyOS APP/HAP artifact validation

**Files:**
- Create: `src/storehelper/stores/harmonyos/__init__.py`
- Create: `src/storehelper/stores/harmonyos/package.py`
- Create: `tests/unit/test_harmonyos_package.py`

**Interfaces:**
- `validate_harmonyos_artifact(path: Path) -> ArtifactInfo`.
- Streamed SHA-256 and bounded safe logical name.

- [ ] Create small valid `.app` and `.hap` ZIP fixtures in tests without adding binary fixtures to git.
- [ ] Add failing cases for wrong suffix, empty file, directory, corrupt ZIP, traversal member, oversized stat, `.app` missing `pack.info`, and `.app` missing embedded HAP.
- [ ] Run `python -m pytest tests/unit/test_harmonyos_package.py -q` and verify missing implementation failure.
- [ ] Implement validation without extracting archives or reading the whole package into memory.
- [ ] Reuse generic hashing/name helpers where doing so does not weaken APK/AAB validation.
- [ ] Run focused tests plus Huawei package tests.
- [ ] Commit: `feat: validate HarmonyOS APP and HAP artifacts`.

### Task 6: HarmonyOS authenticated API client and app verification

**Files:**
- Create: `src/storehelper/stores/harmonyos/client.py`
- Create: `src/storehelper/stores/harmonyos/adapter.py`
- Create: `tests/unit/test_harmonyos_adapter.py`
- Create: `tests/integration/test_harmonyos_verification.py`
- Modify: `src/storehelper/stores/huawei/client.py`

**Interfaces:**
- v2 base `https://connect-api.cloud.huawei.com/api/publish/v2`.
- v3 base `https://connect-api.cloud.huawei.com/api/publish/v3`.
- Shared authenticated JSON request behavior with one refresh, bounded transient retry, no redirects, and safe errors.
- `verify(... packageTypes="7")` accepting observed `value`, compatible `appId`, or scalar ID entries.

- [ ] Add failing contract tests for exact method/path/query/header behavior and every safe app ID representation.
- [ ] Add failures for mismatch, non-JSON, redirect, 401 refresh, exhausted 429/5xx, and response-body non-leakage.
- [ ] Run the new tests and verify missing client failure.
- [ ] Extract only genuinely common authenticated Huawei request behavior; do not merge v2/v3 business endpoints.
- [ ] Implement HarmonyOS verification and generic verified application return type.
- [ ] Run HarmonyOS verification and all Huawei client tests.
- [ ] Commit: `feat: verify existing HarmonyOS applications`.

### Task 7: Secure streamed OBS upload and package binding

**Files:**
- Modify: `src/storehelper/stores/harmonyos/client.py`
- Modify: `src/storehelper/stores/harmonyos/adapter.py`
- Create: `tests/integration/test_harmonyos_upload.py`
- Modify: `tests/unit/test_harmonyos_adapter.py`
- Modify: `tests/unit/test_domain.py`

**Interfaces:**
- `GET v2/upload-url/for-obs` allocation.
- Streamed signed `PUT` to OBS.
- `PUT v3/app-package-info` binding and durable `packageId`.

- [ ] Add a failing full contract test asserting exact allocation parameters and binding body.
- [ ] Assert OBS receives only returned signed headers, receives no Huawei bearer header, and is not redirected.
- [ ] Assert upload uses a stream/file object rather than `read()` bytes.
- [ ] Add failures for non-HTTPS URL, URL user info, non-PUT method, missing headers/object ID/package ID, OBS redirect/error, and vendor rejection.
- [ ] Add redaction tests containing AWS4 authorization, signed query values, OBS URL, and object ID.
- [ ] Implement sanitized allocation models using `SecretStr` for temporary values.
- [ ] Implement deterministic streamed upload and bind; return only `UploadedArtifact(artifact_id=package_id)`.
- [ ] Run upload, redaction, Huawei upload, Ruff, and mypy checks.
- [ ] Commit: `feat: stream HarmonyOS packages through OBS`.

### Task 8: HarmonyOS processing, release notes, submission, and status

**Files:**
- Modify: `src/storehelper/stores/harmonyos/adapter.py`
- Modify: `src/storehelper/stores/harmonyos/client.py`
- Create: `tests/unit/test_harmonyos_processing.py`
- Create: `tests/integration/test_harmonyos_submission.py`

**Interfaces:**
- v2 package-info processing query.
- v3 language-info update containing exactly `lang` and `newFeatures`.
- v3 app-submit and app-info operations.
- Generic processing and review states.

- [ ] Add table-driven failing tests for missing/empty/populated package info, recognized processing/failure fields, unknown explicit states, and bounded failure reason.
- [ ] Add failing HTTP contract tests proving no application metadata beyond release notes is sent.
- [ ] Add submit success, package-still-processing translation, rejection, and review-state mapping tests.
- [ ] Run new tests and verify missing methods fail.
- [ ] Implement conservative response parsing and generic `ArtifactStillProcessingError` translation.
- [ ] Implement only release notes, formal full-release submission, and read-only review status.
- [ ] Run all HarmonyOS and Huawei adapter tests.
- [ ] Commit: `feat: complete HarmonyOS review submission operations`.

### Task 9: Static adapter registry, runtime factory, and CLI integration

**Files:**
- Create: `src/storehelper/stores/registry.py`
- Create: `src/storehelper/runtime.py`
- Modify: `src/storehelper/cli.py`
- Modify: `src/storehelper/__init__.py`
- Create: `tests/unit/test_store_registry.py`
- Modify: `tests/integration/test_cli_publish.py`
- Modify: `tests/integration/test_cli_foundation.py`

**Interfaces:**
- Immutable registrations for `huawei` and `harmonyos`.
- Both registrations resolve the Huawei Service Account credential kind.
- One runtime factory used by publish, resume, status, and credential verification.

- [ ] Add failing tests for registry listing, unknown store, unconfigured store, shared credential profile, and correct adapter/validator/capability selection.
- [ ] Add failing CLI tests for HarmonyOS dry-run, no-submit, full submit, JSON, timeout, resume, status, prompts, and help text.
- [ ] Run focused tests and verify Huawei-only guards fail them.
- [ ] Implement runtime construction with injected HTTP/keyring/repository dependencies for tests.
- [ ] Replace Huawei-specific CLI construction and messages with selected-store labels.
- [ ] Make resume read the receipt first, resolve its store, then validate the selected application target.
- [ ] Preserve credential command storage and CI environment compatibility.
- [ ] Run all CLI integration tests and static checks.
- [ ] Commit: `feat: expose HarmonyOS through the multi-store CLI`.

### Task 10: End-to-end recovery and security regression suite

**Files:**
- Create: `tests/integration/test_harmonyos_publish_flow.py`
- Modify: `tests/integration/test_cli_publish.py`
- Modify: `tests/unit/test_publishing_service.py`
- Modify: `tests/unit/test_run_repository.py`

**Acceptance scenarios:**
- successful `.app` full submit;
- successful `.hap` no-submit;
- dry-run with zero network calls;
- processing timeout and resume without upload;
- submit eventual consistency, repoll, and retry without upload;
- interruption after binding and subsequent resume;
- changed local artifact rejection;
- legacy Huawei v1 receipt migration and resume;
- secret scan of text, JSON, exceptions, and receipt files.

- [ ] Write the failing scenarios using respx and injected clock/sleeper.
- [ ] Run the new integration file and verify gaps.
- [ ] Apply only the minimal production corrections needed by the scenarios.
- [ ] Run `python -m pytest tests/integration tests/unit -q`.
- [ ] Run a repository scan for known private reference values and temporary upload keys.
- [ ] Commit: `test: cover HarmonyOS publishing recovery and security`.

### Task 11: Documentation and public progress tracking

**Files:**
- Modify: `docs/DEVELOPMENT_PLAN.md`
- Modify: `README.md`
- Modify: `examples/storehelper.yaml`
- Create: `docs/HARMONYOS_MANUAL_TEST.md`
- Modify: `CHANGELOG.md` if present

- [ ] Add the complete adapter rollout and mark only verified tasks complete.
- [ ] Document dual-store configuration, shared credentials, `.app`/`.hap`, release notes, dry-run, no-submit, timeout, resume, status, JSON, and security boundaries.
- [ ] Add an opt-in live test checklist that starts with dry-run and no-submit before any review submission.
- [ ] Verify every documented command against installed CLI help.
- [ ] Check all examples contain fake IDs, package names, and profiles only.
- [ ] Run markdown/link checks available in the repository.
- [ ] Commit: `docs: document HarmonyOS publishing workflow`.

### Task 12: Full verification and milestone closure

**Files:**
- Modify only files required by fresh verification findings.
- Modify: `docs/DEVELOPMENT_PLAN.md` after all gates pass.

- [ ] Run `ruff format --check .`.
- [ ] Run `ruff check .`.
- [ ] Run `mypy src`.
- [ ] Run `python -m pytest --cov=storehelper --cov-report=term-missing --cov-fail-under=90`.
- [ ] Build source and wheel in a clean output directory.
- [ ] Install the wheel into a temporary isolated environment.
- [ ] Run installed `storehelper --help`, `version`, dual-store `config validate`, Huawei dry-run, and HarmonyOS dry-run.
- [ ] Run `git diff --check` and confirm the worktree contains no generated build/cache artifacts.
- [ ] Review the complete diff for private-project content, hard-coded credentials, temporary URLs, and vendor payload leakage.
- [ ] Mark the multi-store foundation and HarmonyOS milestones complete in `docs/DEVELOPMENT_PLAN.md` only after every gate passes.
- [ ] Commit: `chore: complete HarmonyOS milestone verification`.

## Completion rule

The increment is complete only when Tasks 1-12 are checked, the full clean verification is fresh, and the development plan marks both the multi-store foundation and HarmonyOS adapter complete. Automated tests do not authorize a live AppGallery upload or review submission. Live verification remains a user-run opt-in action using the documented checklist.
