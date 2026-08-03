# OPPO Software Store v0.6.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish one signed APK update to an existing OPPO Software Store application, submit it
for review safely, and query review status through the StoreHelper CLI and Python API.

**Architecture:** Add a generic staged, non-resumable submission protocol that keeps OPPO's token,
listing snapshot, upload URL, and upload result only in adapter memory. Persist
`submission_started` immediately before the final review mutation so only genuinely ambiguous
final requests block a retry. Keep all existing store flows unchanged.

**Tech Stack:** Python 3.11+, Pydantic v2, httpx, Typer, keyring, pytest, pytest-asyncio, respx,
Ruff, mypy, Hatchling/build, Twine.

## Global Constraints

- Existing OPPO application updates only; one signed APK; maximum 2 GiB.
- Public config contains only the application-specific profile, positive `version_code`, and locale.
- Credentials are exactly `client_id` plus `client_secret` in an independent secure namespace.
- Fixed API origin, HTTPS allowlisted upload hosts, redirects rejected, zero mutation retries.
- Token, secrets, signatures, listing snapshots, upload URLs, request bodies, and raw responses are
  never logged, rendered, or persisted.
- `--no-submit` and `resume` are unsupported; final response loss becomes non-resumable uncertainty.
- Automated tests and release gates never contact OPPO.
- Each task begins with a failing test and ends with a focused commit.

---

### Task 1: Staged submission orchestration

**Files:**
- Modify: `src/storehelper/stores/base.py`
- Modify: `src/storehelper/stores/models.py`
- Modify: `src/storehelper/publishing/service.py`
- Modify: `tests/unit/test_publishing_service.py`
- Modify: `tests/unit/test_store_models.py`

**Interfaces:**
- Produces: `StoreCapabilities.staged_submission: bool = False`.
- Produces: `StagedStoreAdapter.stage_submission(...) -> None` and
  `commit_staged_submission(...) -> str`.
- Preserves: direct `AtomicStoreAdapter` and normal `StoreAdapter` behavior.

- [x] Write failing tests proving stage runs before `submission_started`, commit runs after it,
  staging failures/cancellation become `failed`, and final network loss/cancellation becomes
  `submission_uncertain`.
- [x] Write failing tests proving staged stores reject `--no-submit`, reject `resume`, block a
  duplicate only after ambiguous final submission, and never persist staged context.
- [x] Add the capability and protocol with mutually consistent staged/atomic validation.
- [x] Add the staged branch to `Publisher._continue`; keep direct atomic and four resumable store
  branches byte-for-byte behaviorally equivalent.
- [x] Run `pytest tests/unit/test_publishing_service.py tests/unit/test_store_models.py -q` and the
  existing Xiaomi publish-flow tests.
- [x] Commit: `refactor: support staged store submissions safely`.

### Task 2: Strict OPPO target configuration and APK preflight

**Files:**
- Modify: `src/storehelper/config/models.py`
- Modify: `src/storehelper/config/loader.py`
- Modify: `src/storehelper/stores/models.py`
- Modify: `src/storehelper/stores/registry.py`
- Create: `src/storehelper/stores/oppo/__init__.py`
- Create: `src/storehelper/stores/oppo/package.py`
- Modify: `examples/storehelper.yaml`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_store_config.py`
- Modify: `tests/unit/test_store_models.py`
- Modify: `tests/unit/test_store_registry.py`
- Create: `tests/unit/test_oppo_package.py`

**Interfaces:**
- Produces: `StoreName.OPPO`, `CredentialKind.OPPO_API`.
- Produces: `OppoStoreConfig(credential_profile, version_code, language="zh-CN")`.
- Produces: optional `StoreTarget.version_code: int | None`.

- [x] Write failing tests for valid OPPO config, positive integer version code, dotted package,
  BCP-47 locale, unknown/secret field rejection, and all previous example configurations.
- [x] Write failing tests for APK-only, 2 GiB limit, required 1–500 character release notes,
  staged submission, no no-submit, and review-status capability.
- [x] Add strict models and loader mapping with package name as public OPPO `app_id`.
- [x] Reuse the hardened APK ZIP validator and add an OPPO 2 GiB wrapper.
- [x] Run the config, package, loader, model, and registry unit tests.
- [x] Commit: `feat: configure OPPO update targets`.

### Task 3: Application-specific OPPO credentials

**Files:**
- Modify: `src/storehelper/credentials/models.py`
- Modify: `src/storehelper/credentials/providers.py`
- Modify: `src/storehelper/credentials/service.py`
- Modify: `tests/unit/test_credentials.py`
- Create: `tests/unit/test_oppo_credentials.py`
- Modify: `tests/integration/test_cli_foundation.py`

**Interfaces:**
- Produces: `OppoApiCredential(client_id: SecretStr, client_secret: SecretStr)`.
- Produces: file/env/keyring/prompt sources under `STOREHELPER_OPPO_*` and store `oppo`.

- [x] Write failing tests for non-empty/bounded values, secret-safe repr/errors, friendly JSON,
  storage serialization, independent namespace, source precedence, partial variables, and source
  conflicts.
- [x] Add the credential model without exposing either value through `str`, `repr`, or public
  validation errors.
- [x] Add mutually exclusive credentials file, complete environment pair, keyring, and secure
  prompt resolution.
- [x] Add import/list/delete behavior for the new store while preserving every existing namespace.
- [x] Run credential unit and CLI foundation tests.
- [x] Commit: `feat: secure OPPO publishing credentials`.

### Task 4: Deterministic OPPO HMAC authentication

**Files:**
- Create: `src/storehelper/stores/oppo/__init__.py`
- Create: `src/storehelper/stores/oppo/auth.py`
- Create: `tests/unit/test_oppo_auth.py`

**Interfaces:**
- Produces: `canonical_query(params: Mapping[str, object]) -> str`.
- Produces: `sign_params(secret: SecretStr, params: Mapping[str, object]) -> str`.
- Produces: `OppoAuth` token cache and `signed_params(...)`.

- [x] Write failing tests for ASCII key order, empty/None omission, literal unencoded signing
  values, `api_sign` exclusion, UTF-8 HMAC-SHA256 lowercase hex, deterministic timestamps, token
  expiry forms, forced refresh, and secret-safe repr.
- [x] Implement canonicalization and signing with the standard library only.
- [x] Implement memory-only token handling; never make auth objects printable with secrets/tokens.
- [x] Run `pytest tests/unit/test_oppo_auth.py -q`, Ruff, and mypy for the new module.
- [x] Commit: `feat: implement OPPO request signing`.

### Task 5: Safe token and existing-application queries

**Files:**
- Create: `src/storehelper/stores/oppo/errors.py`
- Create: `src/storehelper/stores/oppo/models.py`
- Create: `src/storehelper/stores/oppo/client.py`
- Create: `tests/unit/test_oppo_errors.py`
- Create: `tests/integration/test_oppo_query.py`

**Interfaces:**
- Produces: `OppoClient.ensure_token()`, `application_info(package_name)`, and safe response parser.
- Produces: immutable public `OppoApplicationInfo` with only fields needed for validation/submission.

- [x] Write failing tests for the exact HTTPS origin, token request, redirect rejection, bounded
  429/5xx read retries, forced token refresh once on auth failure, malformed JSON, safe known/unknown
  errno mapping, and complete redaction.
- [x] Write failing tests for exact package match, current numeric version, audit state, and every
  required existing listing field; reject missing/mismatched/incomplete applications.
- [x] Implement a fixed-host client that never logs query strings, bodies, headers, or responses.
- [x] Implement deterministic status/error parsing with only allowlisted public values.
- [x] Run OPPO query/error tests, Ruff, and mypy.
- [x] Commit: `feat: verify OPPO application access`.

### Task 6: Safe upload-host validation and streamed APK upload

**Files:**
- Modify: `src/storehelper/stores/oppo/client.py`
- Create: `src/storehelper/stores/oppo/package.py`
- Create: `tests/unit/test_oppo_package.py`
- Create: `tests/integration/test_oppo_upload.py`

**Interfaces:**
- Produces: `validate_oppo_artifact(path) -> ArtifactInfo`.
- Produces: `OppoClient.upload_apk(artifact) -> OppoUploadedApk` held only in memory.

- [x] Write failing tests for APK structure, suffix, 2 GiB boundary, streamed SHA-256 reuse and
  protocol-required streamed MD5.
- [x] Write failing tests for upload-address request, multipart `type`/`sign`/`file`, exact logical
  filename, no body buffering, malformed upload result, redirect, and zero upload retries.
- [x] Write failing tests rejecting HTTP, userinfo, fragments, non-default ports, literal/private/
  reserved hosts, and host suffix lookalikes while accepting explicit OPPO/HeyTap suffixes.
- [x] Implement validation, streamed digest, and upload with the returned URL/file URL confined to
  immutable in-memory objects whose repr is redacted.
- [x] Run OPPO package/upload tests plus existing Android validator tests.
- [x] Commit: `feat: upload OPPO APKs safely`.

### Task 7: Staged OPPO submission and review status

**Files:**
- Create: `src/storehelper/stores/oppo/adapter.py`
- Modify: `src/storehelper/stores/oppo/client.py`
- Create: `tests/integration/test_oppo_submission.py`
- Create: `tests/unit/test_oppo_status.py`

**Interfaces:**
- Produces: `OppoAdapter` implementing `StagedStoreAdapter`.
- Produces: exact signed `/resource/v1/app/upd` form and `ReviewStatus` mapping.

- [x] Write failing tests that `verify` requires exact ownership, `version_code` greater than the
  current value, and complete existing metadata.
- [x] Write failing tests for compact one-item `apk_url`, reused listing fields, release-note/test
  description bounds, `online_type=1`, signed form, successful package submission ID, vendor
  rejection, redirect, cancellation, response loss, and zero final retries.
- [x] Write failing tests for every documented audit-status mapping and unknown values.
- [x] Implement staging as fresh info query plus upload retained only in memory; final commit must
  refuse missing/mismatched staged context.
- [x] Run OPPO submission/status tests, Ruff, and mypy.
- [x] Commit: `feat: submit OPPO updates for review`.

### Task 8: Registry, runtime, CLI, and end-to-end safety

**Files:**
- Modify: `src/storehelper/stores/registry.py`
- Modify: `src/storehelper/runtime.py`
- Modify: `src/storehelper/cli.py`
- Create: `tests/integration/test_oppo_cli.py`
- Create: `tests/integration/test_oppo_publish_flow.py`
- Modify: `tests/unit/test_store_registry.py`
- Modify: `tests/unit/test_renderers.py`
- Modify: `tests/unit/test_run_repository.py`

**Interfaces:**
- Produces: audited OPPO factory and CLI choice for publish/status/credentials.
- Preserves: stable `OperationResult` schema version 1 and receipt schema version 5.

- [x] Write failing CLI tests for help/store choices, config validation, credential import/list/
  delete/verify, dry-run zero credential/network access, confirmation/`--yes`, notes requirement,
  no-submit rejection before credentials/network, status, and wrong credential kind.
- [x] Write failing end-to-end tests for success, staging failure safe retry, final response loss,
  cancellation, simulated hard crash, ambiguous duplicate blocking, no resume, receipt deletion,
  deliberate new run, and status reconciliation.
- [x] Register the strict factory/capabilities/validator and connect credential resolution.
- [x] Verify text/JSON output and receipts omit client values, token, signatures, upload values,
  listing data, signed forms, and raw responses.
- [x] Run all OPPO tests and the full five-store regression suite.
- [x] Commit: `feat: expose OPPO publishing through the CLI`.

### Task 9: Documentation and v0.6.0

**Files:**
- Modify: `README.md`
- Modify: `docs/SECURITY_GUIDE.md`
- Modify: `docs/DEVELOPMENT_PLAN.md`
- Create: `docs/OPPO_MANUAL_TEST.md`
- Modify: `examples/storehelper.yaml`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/storehelper/__init__.py`
- Modify: version tests

**Interfaces:**
- Produces: bilingual public setup/security/live-verification instructions and version `0.6.0`.

- [ ] Document application API-client creation, application-specific keyring profile, public
  version config, APK/note constraints, listing reuse, no-submit/no-resume, status, and uncertain
  reconciliation.
- [ ] Add an opt-in checklist ordered local config/dry-run → read-only credential/application
  verification → separately approved publish → status/console reconciliation.
- [ ] Verify examples use only fake identifiers/placeholders and no secret-like values.
- [ ] Bump every active version source and lock metadata to `0.6.0`.
- [ ] Run documentation config tests, version tests, Ruff, mypy, and non-test secret scan.
- [ ] Commit: `docs: document OPPO publishing`.

### Task 10: Full verification and milestone closure

**Files:**
- Modify: `docs/DEVELOPMENT_PLAN.md`
- Modify: this plan's checkboxes

**Interfaces:**
- Produces: independently reproducible v0.6.0 release evidence.

- [ ] Run Ruff format/check, strict mypy, and all tests with coverage at least 90%.
- [ ] Build v0.6.0 sdist/wheel in a fresh temporary directory and run Twine checks.
- [ ] Inspect both archives for worktrees, caches, credentials, private data, and unexpected files.
- [ ] Install the exact wheel into a fresh Python environment with TLS verification enabled.
- [ ] Run installed help/version, six-store config validation, and six offline dry-runs.
- [ ] Run final non-test secret scan and verify no automated test contacted OPPO.
- [ ] Mark the OPPO milestone complete only after every gate exits zero.
- [ ] Commit: `chore: complete OPPO milestone verification`.

## Completion rule

OPPO v0.6.0 is complete only when Tasks 1–10 are checked, all five existing stores remain green,
coverage is at least 90%, and the exact installed wheel passes all six offline store smokes. Live
token/query/upload/submission calls remain manual and opt-in.
