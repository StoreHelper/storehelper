# HONOR App Market v0.8.0 Implementation Plan

> Execute task-by-task with test-driven development. Every automated test remains offline.

**Goal:** Publish one APK update to an existing HONOR application, safely recover interrupted
steps, submit it for review, and query the current review status.

**Architecture:** Reuse the standard resumable publisher with persisted APPID/object/SHA operation
context. Add fixed-host OAuth-style client credentials, an exact package/application reader, a
streamed fixed-host upload, and read-before-write reconciliation for binding, localized notes, and
audit submission.

## Global constraints

- Existing mainland-China phone app, one APK, strictly smaller than 4 GiB.
- Public config contains only credential profile, positive version code, and locale.
- Credentials are `client_id` and `client_secret` in `storehelper:honor`.
- Fixed HTTPS hosts, redirects rejected, bounded read retries, zero mutation retries.
- Secrets, tokens, app snapshots/secretKey, full bodies, and raw responses are never persisted.
- `--no-submit` is unsupported; interrupted normal publishing is resumable by reconciliation.
- No automated test or release gate contacts HONOR.

### Task 1: Design and implementation plan

- [x] Record official API, release, and review documentation.
- [x] Freeze the supported protocol, configuration, credential, retry, and recovery boundaries.
- [x] Define exact status/conflict mapping and release gates.
- [x] Commit: `docs: design HONOR publishing`.

### Task 2: Strict target configuration and APK validation

- [ ] Add `StoreName.HONOR`, `CredentialKind.HONOR_API`, target config/resolution, registry metadata,
  and secret-free examples.
- [ ] Enforce existing-package, positive version, BCP-47 locale, APK-only, and strict `<4 GiB`.
- [ ] Reject `--no-submit` while retaining standard resume/status capabilities.
- [ ] Test first; run focused tests, Ruff, and mypy.
- [ ] Commit: `feat: configure HONOR update targets`.

### Task 3: Credentials and account token

- [ ] Add secret-safe credential model and file/env/keyring/prompt precedence.
- [ ] Implement fixed-host client-credentials token exchange, expiry cache, one forced refresh, and
  bounded/safe response parsing.
- [ ] Cover mixed/partial sources, CLI lifecycle, redaction, redirects, errors, and concurrency.
- [ ] Run focused tests, Ruff, and mypy.
- [ ] Commit: `feat: secure HONOR API authentication`.

### Task 4: Application discovery, detail, release, and status

- [ ] Implement exact package-to-APPID, detail, and current-release reads.
- [ ] Validate package ownership, existing published version, selected locale, configured higher
  version, and conservative conflict states.
- [ ] Implement audit status mapping including unknown values.
- [ ] Cover fixed queries, retries, malformed/oversized responses, safe errors, and redaction.
- [ ] Commit: `feat: verify HONOR application access`.

### Task 5: Allocation and streamed APK upload

- [ ] Allocate exactly one `fileType=100` object using logical name, size, and SHA-256.
- [ ] Stream one multipart `file` to the fixed HONOR upload endpoint with zero mutation retries.
- [ ] Validate/persist only `APPID` and `objectId:sha256`; ignore dynamic upload URL.
- [ ] Cover interruption, response loss, redirects, no bearer forwarding, and safe new allocation.
- [ ] Commit: `feat: upload HONOR application packages`.

### Task 6: Reconciled binding, notes, and audit submission

- [ ] Bind only the APK object and reconcile by file type/SHA-256 before replay.
- [ ] Preserve locale listing fields, update only `newFeature`, and use `setAll=0`.
- [ ] Submit only non-forced immediate full release fields and reconcile ambiguous results by
  version/release ID without automatic duplicate mutation.
- [ ] Cover every partial failure, resume boundary, conflict, and status transition.
- [ ] Commit: `feat: submit HONOR updates for review`.

### Task 7: Registry, runtime, CLI, and end-to-end safety

- [ ] Register factory/credentials and expose publish/resume/status/credential commands.
- [ ] Cover dry-run, confirmation, notes, unsupported no-submit, installed output, and wrong kind.
- [ ] Prove receipt/log/JSON redaction and end-to-end recovery with stateful mock backend.
- [ ] Run the full eight-store regression suite.
- [ ] Commit: `feat: expose HONOR publishing through the CLI`.

### Task 8: Documentation and v0.8.0

- [ ] Update README, security/development plans, examples, troubleshooting, CI credentials, and
  bilingual manual live-verification checklist.
- [ ] Bump every active version source and lock metadata to `0.8.0`.
- [ ] Run documentation/config/version tests, Ruff, mypy, and non-test secret scan.
- [ ] Commit: `docs: document HONOR publishing`.

### Task 9: Full verification and milestone closure

- [ ] Run Ruff format/check, strict mypy, and all tests with coverage at least 90%.
- [ ] Build fresh v0.8.0 sdist/wheel, run Twine, and inspect every archive path.
- [ ] Install the exact wheel and run help/version, config validation, and eight offline dry-runs.
- [ ] Run final non-test secret scan and prove tests use mocked HONOR transports only.
- [ ] Mark the milestone complete only after every gate exits zero.
- [ ] Commit: `chore: complete HONOR milestone verification`.

## Completion rule

HONOR v0.8.0 is complete only when Tasks 1–9 are checked, all seven existing stores remain green,
coverage is at least 90%, and the exact installed wheel passes eight offline store smokes. Live
query/upload/submission calls remain manual and opt-in.
