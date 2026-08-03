# vivo App Store v0.7.0 Implementation Plan

> Execute task-by-task with test-driven development. Every automated test remains offline.

**Goal:** Publish one APK update to an existing vivo application, submit it safely, and query its
review status through StoreHelper.

**Architecture:** Reuse the staged non-resumable orchestration introduced for OPPO. Add a fixed-host
vivo client with deterministic HMAC signing, a strict application query, a streamed temporary APK
upload, and one final update request protected by the existing uncertainty boundary.

## Global constraints

- Existing mainland-China application, one APK, maximum 3 GiB.
- Public config contains only credential profile, positive version code, and locale.
- Credentials are exactly `access_key` and `secret_key` in `storehelper:vivo`.
- Fixed HTTPS gateway, redirects rejected, bounded read retries, zero mutation retries.
- Secrets, signatures, full forms/responses, upload IDs, and file MD5 are never logged or persisted.
- `--no-submit` and `resume` are unsupported.
- No automated test or release gate contacts vivo.

### Task 1: Design and implementation plan

- [x] Record official documentation entry points and the local proven-reference limitation.
- [x] Freeze the supported protocol subset, configuration, credentials, retry, and uncertainty
  boundaries.
- [x] Define review mapping and exact release gates.
- [x] Commit: `docs: design vivo publishing`.

### Task 2: Strict target configuration and APK validation

**Files:** config models/loader, store models/registry, example YAML, vivo package module, and tests.

- [x] Write failing tests for `StoreName.VIVO`, `CredentialKind.VIVO_API`, strict target fields,
  dotted package identity, BCP-47 locale, secret/unknown rejection, and previous examples.
- [x] Write failing tests for APK-only, structure, 3 GiB boundary, streamed MD5, the release-notes
  requirement, staged submission, no no-submit/resume, and status capability.
- [x] Implement the strict config, target mapping, registry metadata, and validator.
- [x] Run focused config/package/registry tests, Ruff, and mypy.
- [x] Commit: `feat: configure vivo update targets`.

### Task 3: Credentials and deterministic HMAC authentication

**Files:** credential models/providers/service, vivo auth module, CLI foundation tests.

- [x] Write failing tests for secret-safe credential validation/storage, independent namespace,
  file/env/keyring/prompt precedence, mixed/partial sources, and CLI lifecycle.
- [x] Write failing HMAC tests for ASCII order, literal values, `sign` exclusion, empty values,
  UTF-8 lowercase HMAC-SHA256, exact common parameters, and deterministic millisecond timestamps.
- [x] Implement `VivoApiCredential`, provider integration, and secret-safe signer.
- [x] Run focused credential/auth tests, Ruff, and mypy.
- [x] Commit: `feat: secure vivo API authentication`.

### Task 4: Fixed-host application query and safe errors

**Files:** vivo errors/models/client modules and query integration tests.

- [x] Write failing tests for the exact gateway/method, form signing, redirect rejection, bounded
  read retries, malformed/oversized JSON, HTTP failures, code/subCode parsing, and redaction.
- [x] Write failing tests for exact package match, positive current version, known status, and
  minimal immutable public application data.
- [x] Implement the fixed-host client and safe response/error parsing.
- [x] Run query/error tests, Ruff, and mypy.
- [x] Commit: `feat: verify vivo application access`.

### Task 5: Streamed upload, staged final submission, and status

**Files:** vivo client/adapter/package modules and upload/submission/status tests.

- [x] Write failing multipart tests for exact method/fields/logical filename, streamed APK, MD5,
  valid upload result, redirects, cancellation, and zero upload retries.
- [x] Write failing adapter tests for higher version code, allowed/conflicting statuses, fresh query,
  in-memory staged context, exact seven final fields, and no listing mutation.
- [x] Write failing tests for final success/rejection/redirect/response loss/cancellation, zero final
  retries, and all status mappings including unknown.
- [x] Implement staged upload/final submission and review status.
- [x] Run focused tests, Ruff, and mypy.
- [x] Commit: `feat: submit vivo updates for review`.

### Task 6: Registry, runtime, CLI, and end-to-end safety

**Files:** registry/runtime/CLI, renderers/repository redaction, integration publish-flow tests.

- [x] Write failing CLI tests for choices, config, credential lifecycle/verification, dry-run,
  confirmation, notes requirement, unsupported no-submit/resume, status, and wrong credential kind.
- [x] Write end-to-end tests for success, safe staging failure, ambiguous final failure/cancellation/
  crash, duplicate blocking, receipt deletion, deliberate new run, and status reconciliation.
- [x] Register the audited factory and wire credential/runtime resolution.
- [x] Prove text/JSON output and receipts omit all vivo sensitive/transient values.
- [x] Run all vivo tests and the full six-store regression suite.
- [x] Commit: `feat: expose vivo publishing through the CLI`.

### Task 7: Documentation and v0.7.0

**Files:** README, security/development plans, manual checklist, example, active version sources.

- [ ] Document setup, credential storage, config, scope, notes/version constraints, status,
  unsupported operations, and uncertainty reconciliation in English plus Chinese guidance.
- [ ] Add a local-first, separately approved live-verification checklist.
- [ ] Bump every active version source and lock metadata to `0.7.0`.
- [ ] Run documentation/config/version tests, Ruff, mypy, and non-test secret scan.
- [ ] Commit: `docs: document vivo publishing`.

### Task 8: Full verification and milestone closure

- [ ] Run Ruff format/check, strict mypy, and all tests with coverage at least 90%.
- [ ] Build v0.7.0 sdist/wheel in a fresh temporary directory and run Twine checks.
- [ ] Inspect archives for worktrees, caches, credentials, private data, and unexpected files.
- [ ] Install the exact wheel into a fresh Python environment with TLS verification enabled.
- [ ] Run installed help/version, seven-store config validation, and seven offline dry-runs.
- [ ] Run final non-test secret scan and prove no automated test contacted vivo.
- [ ] Mark the vivo milestone complete only after every gate exits zero.
- [ ] Commit: `chore: complete vivo milestone verification`.

## Completion rule

vivo v0.7.0 is complete only when Tasks 1–8 are checked, all six existing stores remain green,
coverage is at least 90%, and the exact installed wheel passes all seven offline store smokes. Live
query/upload/submission calls remain manual and opt-in.
