# Google Play v0.4.0 Implementation Plan

**Design:** `docs/superpowers/specs/2026-08-03-google-play-design.md`
**Branch:** `codex/google-play`
**Rule:** Every task starts with a failing test, keeps existing stores green, and ends in one
reviewable commit. Automated tests never call Google.

## Progress

### Task 1: Durable operation contract and receipt schema v4

**Files:**
- Modify: `src/storehelper/stores/models.py`
- Modify: `src/storehelper/stores/base.py`
- Modify: `src/storehelper/publishing/service.py`
- Modify: `src/storehelper/runs/models.py`
- Modify: `src/storehelper/runs/repository.py`
- Modify: all existing adapters and their tests

**Interfaces:**

```python
class UploadedArtifact(BaseModel):
    artifact_id: str
    operation_id: str | None = None

async def prepare_release(
    *, target: StoreTarget, artifact_id: str, operation_id: str | None,
    release_notes: str | None,
) -> None: ...

async def submit(
    *, target: StoreTarget, artifact_id: str, operation_id: str | None,
) -> str: ...
```

- [x] Add failing tests for `operation_id`, target `track`/`release_status`, v1-v3 receipt
  migration, v4 round-trip, target mismatch, and all existing adapter calls.
- [x] Persist `operation_id`, `track`, and `release_status` atomically.
- [x] Pass the operation ID through processing, preparation, and submission without changing
  Huawei/HarmonyOS/Apple HTTP contracts.
- [x] Run all existing store tests, Ruff, and mypy.
- [x] Commit: `refactor: add durable store operation context`.

### Task 2: Strict Google Play configuration

**Files:**
- Modify: `src/storehelper/config/models.py`
- Modify: `src/storehelper/config/loader.py`
- Modify: `src/storehelper/stores/models.py`
- Modify: `tests/unit/test_store_config.py`
- Modify: `tests/unit/test_config.py`
- Modify: `examples/storehelper.yaml`

- [x] Add failing tests for `google_play`, required credential profile/track/status, safe track
  identifiers, BCP-47 language, unknown fields, and multi-store compatibility.
- [x] Add `StoreName.GOOGLE_PLAY` and required `draft|completed` release status.
- [x] Resolve package name as both Google application ID and package name.
- [x] Keep schema version `1` and preserve every previous valid configuration.
- [x] Commit: `feat: configure Google Play targets`.

### Task 3: Google service-account credentials

**Files:**
- Modify: `src/storehelper/credentials/models.py`
- Modify: `src/storehelper/credentials/providers.py`
- Modify: `src/storehelper/credentials/service.py`
- Modify: `tests/unit/test_credentials.py`
- Modify: `tests/integration/test_cli_foundation.py`

- [x] Add failing tests for standard JSON import, RSA validation, type/client-email/token-URI
  validation, independent keyring namespace, CI file/variables, conflicts, prompt, and redaction.
- [x] Add `CredentialKind.GOOGLE_SERVICE_ACCOUNT` and `GoogleServiceAccount`.
- [x] Support `STOREHELPER_GOOGLE_*` sources with fixed precedence.
- [x] Verify list/delete/import CLI semantics remain backward compatible.
- [x] Commit: `feat: secure Google Play credentials`.

### Task 4: RS256 OAuth assertion and token exchange

**Files:**
- Create: `src/storehelper/stores/google_play/__init__.py`
- Create: `src/storehelper/stores/google_play/auth.py`
- Create: `tests/unit/test_google_play_auth.py`

- [x] Add failing tests for JWT header/claims/signature, Android Publisher scope, one-hour maximum,
  cached access token, expiry margin, malformed token response, and secret-safe repr/errors.
- [x] Implement direct HTTPS form exchange without adding a Google SDK dependency.
- [x] Keep assertions and access tokens in memory only.
- [x] Commit: `feat: authenticate Google Play service accounts`.

### Task 5: Google errors, lifecycle status, and read-only verification

**Files:**
- Create: `src/storehelper/stores/google_play/errors.py`
- Create: `src/storehelper/stores/google_play/client.py`
- Create: `src/storehelper/stores/google_play/adapter.py`
- Create: `tests/unit/test_google_play_errors.py`
- Create: `tests/unit/test_google_play_status.py`
- Create: `tests/integration/test_google_play_verification.py`

- [x] Add failing tests for sanitized Google error shapes, redirect rejection, retry bounds, JWT
  refresh, lifecycle mapping, multiple-release priority, package/track encoding, and empty access.
- [x] Implement fixed-host authenticated JSON requests and read-only
  `applications.tracks.releases.list` verification/status.
- [x] Never create an Edit for credential verification or status.
- [x] Commit: `feat: verify Google Play targets and status`.

### Task 6: Streamed APK/AAB upload inside an App Edit

**Files:**
- Create: `src/storehelper/stores/google_play/package.py`
- Modify: `src/storehelper/stores/google_play/client.py`
- Modify: `src/storehelper/stores/google_play/adapter.py`
- Create: `tests/integration/test_google_play_upload.py`

- [ ] Add failing tests for Edit creation, AAB/APK endpoint selection, `uploadType=media`, streamed
  bytes, no redirect, long write timeout compatibility, positive version code, SHA-256 match,
  malformed response, and response loss.
- [ ] Reuse the hardened Android local validator.
- [ ] Return `versionCode` plus public Edit ID and persist neither expiry nor response bodies.
- [ ] Commit: `feat: upload Google Play artifacts through app edits`.

### Task 7: Safe track preparation and optional notes

**Files:**
- Modify: `src/storehelper/stores/google_play/client.py`
- Modify: `src/storehelper/stores/google_play/adapter.py`
- Create: `tests/integration/test_google_play_track.py`

- [ ] Add failing tests for missing operation ID, Edit existence, track GET, staged/halted rejection,
  draft preservation, completed replacement, exact version code, locale, optional notes, 1-500
  length, no unrelated fields, and idempotent retry.
- [ ] Update only the configured track.
- [ ] Reject active staged rollouts rather than overwriting them.
- [ ] Commit: `feat: prepare Google Play track releases`.

### Task 8: Validate, commit, and reconcile submission

**Files:**
- Modify: `src/storehelper/stores/google_play/client.py`
- Modify: `src/storehelper/stores/google_play/adapter.py`
- Create: `tests/integration/test_google_play_submission.py`

- [ ] Add failing tests for validate-before-commit, explicit `ERROR_IF_IN_REVIEW`,
  `changesNotSentForReview=false`, already-visible version, lost commit response, bounded lifecycle
  reconciliation, expired Edit, 409 conflict, and no second upload.
- [ ] Treat a lifecycle-visible version as successful commit reconciliation.
- [ ] Never use Google's cancel-existing-review default.
- [ ] Commit: `feat: commit Google Play releases safely`.

### Task 9: Registry, runtime, and CLI integration

**Files:**
- Modify: `src/storehelper/stores/registry.py`
- Modify: `src/storehelper/runtime.py`
- Modify: `src/storehelper/cli.py`
- Create: `tests/integration/test_google_play_cli.py`
- Modify: existing CLI tests

- [ ] Add failing tests for help choices, credential import/verify, dry-run, no-submit, full JSON
  draft/completed publish, confirmation, timeout/recovery, status, and wrong credential kind.
- [ ] Register the audited adapter and credential kind.
- [ ] Keep output schemas and prior store defaults unchanged.
- [ ] Run all CLI tests, Ruff, and mypy.
- [ ] Commit: `feat: expose Google Play publishing through the CLI`.

### Task 10: End-to-end recovery and security regression

**Files:**
- Create: `tests/integration/test_google_play_publish_flow.py`
- Modify: `tests/unit/test_publishing_service.py`
- Modify: `tests/unit/test_run_repository.py`

**Acceptance scenarios:**

```text
valid AAB full draft and completed commits
APK upload
no-submit stops before track update
dry-run performs zero I/O
track/commit network interruption resumes without upload
commit response loss reconciles lifecycle version
expired Edit is actionable
changed artifact/track/status rejection before network
v1/v2/v3 receipt migration and v4 rewrite
no Google key, assertion, token, or raw error in output/receipt
```

- [ ] Implement all scenarios with generated keys, fake IDs, injected clock/sleeper, and mocked HTTP.
- [ ] Apply only production corrections revealed by the tests.
- [ ] Run the complete suite and non-test secret scan.
- [ ] Commit: `test: cover Google Play recovery and security`.

### Task 11: Documentation and v0.4.0 versioning

**Files:**
- Modify: `README.md`
- Modify: `docs/SECURITY_GUIDE.md`
- Modify: `docs/DEVELOPMENT_PLAN.md`
- Create: `docs/GOOGLE_PLAY_MANUAL_TEST.md`
- Modify: `examples/storehelper.yaml`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/storehelper/__init__.py`
- Modify: version tests

- [ ] Document Play/Cloud setup, permissions, service-account sources, required package/track/status,
  AAB/APK commands, draft/completed risk, optional notes, no-submit expiry, resume, lifecycle status,
  JSON, and security boundaries.
- [ ] Add a live checklist ordered config/dry-run → read-only verify → draft no-submit/commit →
  separately reviewed completed release.
- [ ] Verify examples against installed help and keep all identifiers fake.
- [ ] Bump version consistently to `0.4.0`.
- [ ] Commit: `docs: document Google Play publishing`.

### Task 12: Full verification and milestone closure

- [ ] Run Ruff format/check and mypy.
- [ ] Run all tests with coverage at least 90%.
- [ ] Build sdist/wheel in a fresh temporary directory and run Twine checks.
- [ ] Install the exact wheel in a fresh environment.
- [ ] Run installed help/version, four-store config validation, and four offline dry-runs.
- [ ] Run `git diff --check`, archive-content checks, and private-data/secret review.
- [ ] Mark the Google Play milestone complete only after every fresh command exits zero.
- [ ] Commit: `chore: complete Google Play milestone verification`.

## Completion rule

Google Play v0.4.0 is complete only when Tasks 1-12 are checked, all Huawei/HarmonyOS/Apple
regressions remain green, the exact wheel passes installed four-store dry-runs, and the public
development plan marks the milestone complete. Automated verification never authorizes a real
Google upload, track update, or Edit commit.
