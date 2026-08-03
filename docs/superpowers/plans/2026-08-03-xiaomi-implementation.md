# Xiaomi App Store v0.5.0 Implementation Plan

**Design:** `docs/superpowers/specs/2026-08-03-xiaomi-design.md`
**Branch:** `codex/xiaomi`
**Rule:** Each task starts with a failing test, keeps all completed stores green, and ends in one
reviewable commit. Automated tests never contact Xiaomi.

## Progress

### Task 1: Atomic-submission orchestration and receipt schema v5

**Files:**
- Modify: `src/storehelper/stores/models.py`
- Modify: `src/storehelper/stores/base.py`
- Modify: `src/storehelper/publishing/service.py`
- Modify: `src/storehelper/runs/models.py`
- Modify: `src/storehelper/runs/repository.py`
- Modify: `tests/unit/test_publishing_service.py`
- Modify: `tests/unit/test_run_repository.py`
- Modify: existing capabilities tests

- [x] Add failing tests for atomic success, no-submit rejection before validation/network, start and
  uncertain states, cancellation, hard-crash receipt behavior, duplicate blocking, no resume, and
  v1-v4 migration to v5.
- [x] Add default-compatible `atomic_submission`/`supports_no_submit` capabilities and the separate
  `AtomicStoreAdapter` protocol.
- [x] Persist `submission_started` before mutation and never advertise uncertain runs as resumable.
- [x] Preserve every existing multi-step store flow unchanged.
- [x] Commit: `refactor: support atomic store submissions safely`.

### Task 2: Strict Xiaomi target configuration and icon preflight

**Files:**
- Modify: `src/storehelper/config/models.py`
- Modify: `src/storehelper/config/loader.py`
- Modify: `src/storehelper/stores/models.py`
- Modify: `src/storehelper/stores/registry.py`
- Modify: `src/storehelper/runtime.py`
- Modify: `src/storehelper/cli.py`
- Modify: `examples/storehelper.yaml`
- Modify: configuration/registry/runtime tests

- [x] Add failing tests for `xiaomi`, existing package, app name, credential profile, HTTPS privacy
  URL, relative icon resolution, readable PNG preflight, unknown fields, and prior configurations.
- [x] Add optional target fields and a store-local preflight callable used by normal and dry-run
  publishers.
- [x] Enforce APK-only, 2 GiB, release notes required, atomic submission, no no-submit, and no
  review-status capability.
- [x] Commit: `feat: configure Xiaomi update targets`.

### Task 3: Xiaomi credentials and structured review accounts

**Files:**
- Modify: `src/storehelper/credentials/models.py`
- Modify: `src/storehelper/credentials/providers.py`
- Modify: `tests/unit/test_credentials.py`
- Modify: `tests/integration/test_cli_foundation.py`

- [x] Add failing tests for email/API-secret/X.509 RSA validation, nested structured account rules,
  five-account/audit-note limits, file/env/keyring/prompt precedence, source conflicts, independent
  namespace, and secret-safe repr/errors.
- [x] Add `CredentialKind.XIAOMI_API`, friendly credential JSON, secure storage serialization, and
  exact structured API conversion.
- [x] Support `STOREHELPER_XIAOMI_*` sources without changing existing credential resolution.
- [x] Commit: `feat: secure Xiaomi publishing credentials`.

### Task 4: Vendor-required RSA encryption and digest contract

**Files:**
- Create: `src/storehelper/stores/xiaomi/__init__.py`
- Create: `src/storehelper/stores/xiaomi/auth.py`
- Create: `tests/unit/test_xiaomi_auth.py`

- [x] Add failing tests for exact RequestData/file MD5, signature JSON order, dynamic RSA chunk size,
  multi-block decryption, lowercase hexadecimal, non-RSA/invalid certificate, streamed hashing,
  and secret-safe repr.
- [x] Implement PKCS#1 v1.5 public-key encryption with `cryptography`; add no new crypto package.
- [x] Keep API secret, plaintext signature JSON, and ciphertext out of logs/receipts.
- [x] Commit: `feat: implement Xiaomi request signatures`.

### Task 5: Safe Xiaomi errors and signed read-only query

**Files:**
- Create: `src/storehelper/stores/xiaomi/errors.py`
- Create: `src/storehelper/stores/xiaomi/client.py`
- Create: `src/storehelper/stores/xiaomi/adapter.py`
- Create: `tests/unit/test_xiaomi_errors.py`
- Create: `tests/integration/test_xiaomi_query.py`

- [x] Add failing tests for fixed HTTPS host, redirect rejection, multipart field names, exact query
  JSON, signed request, 429/5xx bounds, malformed response, package mismatch, claim result `-7`,
  update readiness, and raw-error redaction.
- [x] Implement `/dev/query` and use it for credential/application verification.
- [x] Never use query as review-status evidence.
- [x] Commit: `feat: verify Xiaomi update access`.

### Task 6: Streamed single-APK push

**Files:**
- Modify: `src/storehelper/stores/xiaomi/client.py`
- Modify: `src/storehelper/stores/xiaomi/adapter.py`
- Create: `src/storehelper/stores/xiaomi/package.py`
- Create: `tests/integration/test_xiaomi_push.py`

- [ ] Add failing tests for `synchroType=1`, exact appInfo fields, updateDesc, optional structured
  testAccount, APK/icon digests, streamed multipart bytes, 2 GiB limit, success, documented vendor
  errors, redirect, response loss, cancellation, and zero mutation retries.
- [ ] Reuse hardened Android APK validation and add Xiaomi/icon limits.
- [ ] Return only the public package name after `result=0`.
- [ ] Commit: `feat: submit Xiaomi APK updates atomically`.

### Task 7: Registry, runtime, and CLI integration

**Files:**
- Modify: `src/storehelper/stores/registry.py`
- Modify: `src/storehelper/runtime.py`
- Modify: `src/storehelper/cli.py`
- Create: `tests/integration/test_xiaomi_cli.py`
- Modify: existing CLI/registry tests

- [ ] Add failing tests for help choices, credential import/list/delete/verify, dry-run, full JSON
  publish, prompt/`--yes`, release-notes requirement, no-submit early rejection, unsupported status,
  wrong credential kind, and no live I/O.
- [ ] Register the audited Xiaomi factory, local preflight, APK validator, and atomic capabilities.
- [ ] Fail unsupported status before credential resolution.
- [ ] Commit: `feat: expose Xiaomi publishing through the CLI`.

### Task 8: End-to-end uncertain-result and security regression

**Files:**
- Create: `tests/integration/test_xiaomi_publish_flow.py`
- Modify: publishing/repository/output tests where required

- [ ] Cover valid full update, dry-run zero I/O, vendor rejection, lost response, cancellation, start
  state after simulated hard crash, same-artifact blocking, no resume, local receipt deletion, and
  subsequent deliberate retry.
- [ ] Verify no API secret, certificate, SIG, test account, request body, or raw response appears in
  text/JSON output or v5 receipts.
- [ ] Run the complete suite, Ruff, mypy, and non-test secret scan.
- [ ] Commit: `test: cover Xiaomi atomic publishing safety`.

### Task 9: Documentation and v0.5.0

**Files:**
- Modify: `README.md`
- Modify: `docs/SECURITY_GUIDE.md`
- Modify: `docs/DEVELOPMENT_PLAN.md`
- Create: `docs/XIAOMI_MANUAL_TEST.md`
- Modify: `examples/storehelper.yaml`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/storehelper/__init__.py`
- Modify: version tests

- [ ] Document developer-site key generation, existing-app scope, public config, secure review
  accounts, APK/icon/release-note inputs, no sandbox/status/no-submit, confirmation, uncertain-state
  manual reconciliation, and local receipt deletion.
- [ ] Add a live checklist ordered config/dry-run → signed read-only query → separately approved push.
- [ ] Verify all examples use fake identifiers and no secret-like values.
- [ ] Bump version consistently to `0.5.0`.
- [ ] Commit: `docs: document Xiaomi publishing`.

### Task 10: Full verification and milestone closure

- [ ] Run Ruff format/check, mypy, and all tests with coverage at least 90%.
- [ ] Build v0.5.0 sdist/wheel in a fresh temporary directory and run Twine checks.
- [ ] Install the exact wheel in a fresh environment.
- [ ] Run installed help/version, five-store config validation, and five offline dry-runs.
- [ ] Inspect archive contents and complete private-data/secret review.
- [ ] Mark the public Xiaomi milestone complete only after every command exits zero.
- [ ] Commit: `chore: complete Xiaomi milestone verification`.

## Completion rule

Xiaomi v0.5.0 is complete only when Tasks 1-10 are checked, all four existing stores remain green,
and exact-wheel validation passes. No automated gate or developer test may call Xiaomi's live-only
`/dev/push` mutation.
