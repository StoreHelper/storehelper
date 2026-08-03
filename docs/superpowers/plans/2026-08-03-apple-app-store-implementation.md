# Apple App Store Connect v0.3.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add secure, resumable, end-to-end iOS IPA publishing to an existing App Store Connect app and App Store version through Apple's native Build Upload API.

**Architecture:** Extend the store-neutral contracts only where Apple requires a release target and a processing-handle-to-build-ID transition. A typed Apple adapter owns ES256 authentication, JSON:API, IPA inspection, range uploads, release preparation, and review reconciliation. The shared publisher, receipt repository, registry, keyring, CLI, and output schemas remain the single orchestration path.

**Tech Stack:** Python 3.11+, Pydantic v2, cryptography, httpx async streaming, Typer, pytest/pytest-asyncio, Ruff, mypy, build, Twine.

## Global Constraints

- Support existing App Store Connect iOS apps and existing editable App Store versions only.
- Use App Store Connect API 4.1+ native `buildUploads`; never invoke Xcode, altool, or Transporter.
- Accept `.ipa` only; do not build, export, sign, notarize, or modify the archive.
- Support team and individual App Store Connect API keys; private P-256 keys never enter YAML, CLI arguments, logs, results, or receipts.
- Stream every Apple delivery operation with exact returned headers, no bearer token, and no redirects.
- Use the nondeprecated `reviewSubmissions` workflow.
- Automated tests must make zero live Apple or Huawei calls.
- Keep all Huawei Android and HarmonyOS behavior backward compatible.
- Maintain Python 3.11–3.14 support and at least 90% project coverage.

---

## File map

```text
src/storehelper/artifacts/models.py             AppleArtifactInfo
src/storehelper/config/models.py                strict Apple project configuration
src/storehelper/config/loader.py                Apple StoreTarget resolution
src/storehelper/credentials/models.py           typed Huawei/Apple credential union
src/storehelper/credentials/providers.py        per-kind env/keyring/prompt resolution
src/storehelper/credentials/service.py          store-aware import/list/delete
src/storehelper/publishing/service.py            target-aware state machine
src/storehelper/runs/models.py                   receipt schema v3
src/storehelper/runs/repository.py               v1/v2 to v3 migration
src/storehelper/stores/apple/auth.py             ES256 JWT generation/cache
src/storehelper/stores/apple/client.py           safe JSON:API and delivery HTTP
src/storehelper/stores/apple/errors.py           sanitized Apple error mapping
src/storehelper/stores/apple/models.py           range/upload response value models
src/storehelper/stores/apple/package.py          IPA validation and plist inspection
src/storehelper/stores/apple/adapter.py          Apple publishing operations
src/storehelper/stores/base.py                   target-aware adapter protocol
src/storehelper/stores/models.py                 Apple/store-neutral extensions
src/storehelper/stores/registry.py               audited Apple registration
src/storehelper/runtime.py                       credential-union runtime factory
src/storehelper/cli.py                           store-aware credentials and Apple CLI
tests/unit/                                      pure validation/state/auth tests
tests/integration/                               mocked Apple HTTP and CLI flows
docs/APPLE_MANUAL_TEST.md                        opt-in live checklist
```

### Task 1: Target-aware contracts and receipt schema v3

**Files:**
- Modify: `src/storehelper/stores/models.py`
- Modify: `src/storehelper/stores/base.py`
- Modify: `src/storehelper/publishing/service.py`
- Modify: `src/storehelper/runs/models.py`
- Modify: `src/storehelper/runs/repository.py`
- Modify: `src/storehelper/stores/huawei/adapter.py`
- Modify: `src/storehelper/stores/harmonyos/adapter.py`
- Modify: `tests/unit/test_store_models.py`
- Modify: `tests/unit/test_run_repository.py`
- Modify: `tests/unit/test_publishing_service.py`
- Modify: existing Huawei/HarmonyOS integration tests

**Interfaces:**

```python
class StoreTarget(BaseModel):
    store: StoreName
    label: str
    app_id: str
    package_name: str
    credential_profile: str
    language: str
    release_id: str | None = None
    platform: str | None = None


class ProcessingStatus(BaseModel):
    state: ProcessingState
    reason: str | None = None
    artifact_id: str | None = None


class StoreAdapter(Protocol):
    async def verify(self, *, target: StoreTarget) -> VerifiedApplication: ...
    async def upload(self, *, target: StoreTarget, artifact: ArtifactInfo) -> UploadedArtifact: ...
    async def processing_status(
        self, *, target: StoreTarget, artifact_id: str
    ) -> ProcessingStatus: ...
    async def prepare_release(
        self, *, target: StoreTarget, artifact_id: str, release_notes: str | None
    ) -> None: ...
    async def submit(self, *, target: StoreTarget, artifact_id: str) -> str: ...
    async def review_status(self, *, target: StoreTarget) -> ReviewStatus: ...
```

- [x] Write failing serialization, migration, publisher replacement-ID, and adapter contract tests.
- [x] Run the focused tests and confirm old app-ID-only calls fail.
- [x] Add `APPLE`, `APPLE_API_KEY`, optional target fields, and optional processing replacement ID.
- [x] Advance `RunReceipt` to schema v3 with `release_id` and `submission_id`; migrate literal v1 and v2 payloads.
- [x] Refactor Publisher to pass `StoreTarget`, always call `prepare_release` for full submission,
  replace the processing handle when READY returns `artifact_id`, and persist the returned submission ID.
- [x] Adapt Huawei/HarmonyOS implementations without changing their HTTP contracts.
- [x] Run all existing tests, Ruff, and mypy.
- [x] Commit: `refactor: prepare publishing contracts for Apple`.

### Task 2: Strict Apple configuration and registry entry

**Files:**
- Modify: `src/storehelper/config/models.py`
- Modify: `src/storehelper/config/loader.py`
- Modify: `src/storehelper/stores/registry.py`
- Modify: `src/storehelper/runtime.py`
- Modify: `examples/storehelper.yaml`
- Modify: `tests/unit/test_store_config.py`
- Modify: `tests/unit/test_store_registry.py`

**Interfaces:**

```python
class AppleStoreConfig(BaseModel):
    app_id: str
    bundle_id: str
    app_store_version_id: str
    credential_profile: str
    platform: Literal["IOS"] = "IOS"
    language: str = "en-US"
```

- [x] Write failing tests for Apple-only/three-store config, invalid bundle ID, empty version ID,
  unsupported platform, strict extra fields, and resolved target output.
- [x] Run focused tests and confirm `apple` is rejected.
- [x] Add the strict Apple model and `resolve_store_target` mapping to `release_id`/`platform`.
- [x] Add registry metadata for `.ipa`, Apple credentials, processing poll, optional notes, and status.
- [x] Generalize registry factory typing to a tagged `StoreCredential` union without casts at call sites.
- [x] Update only fake values in the example YAML.
- [x] Run configuration/registry tests and static checks.
- [x] Commit: `feat: configure Apple App Store targets`.

### Task 3: Apple credential profiles and ES256 authentication

**Files:**
- Modify: `src/storehelper/credentials/models.py`
- Modify: `src/storehelper/credentials/providers.py`
- Modify: `src/storehelper/credentials/service.py`
- Modify: `src/storehelper/commands/credentials.py`
- Create: `src/storehelper/stores/apple/__init__.py`
- Create: `src/storehelper/stores/apple/auth.py`
- Modify: `tests/unit/test_credentials.py`
- Create: `tests/unit/test_apple_auth.py`

**Interfaces:**

```python
class AppleKeyType(StrEnum):
    TEAM = "team"
    INDIVIDUAL = "individual"


class AppleApiKey(BaseModel):
    key_type: AppleKeyType
    key_id: str
    issuer_id: str | None
    private_key: SecretStr


StoreCredential = HuaweiServiceAccount | AppleApiKey


class CredentialProvider:
    def resolve(
        self, profile: str, kind: CredentialKind, *, interactive: bool
    ) -> StoreCredential: ...
```

- [x] Write failing tests for team/individual JSON, issuer rules, EC P-256 enforcement, type-tagged
  storage, Huawei legacy storage, Apple environment precedence/conflicts, and redacted repr/errors.
- [x] Write deterministic JWT tests that decode headers/claims and verify ES256 signatures.
- [x] Run tests and confirm Apple types/imports are missing.
- [x] Implement typed storage envelopes while accepting legacy untagged Huawei profiles.
- [x] Add Apple file/env/keyring/prompt resolution with mutually exclusive sources.
- [x] Generate ten-minute cached team (`iss`) and individual (`sub=user`) JWTs.
- [x] Run all credential/Huawei auth tests and secret scans.
- [x] Commit: `feat: secure Apple API key credentials`.

### Task 4: IPA artifact inspection

**Files:**
- Modify: `src/storehelper/artifacts/models.py`
- Create: `src/storehelper/stores/apple/package.py`
- Create: `tests/unit/test_apple_package.py`

**Interfaces:**

```python
class AppleArtifactInfo(ArtifactInfo):
    bundle_id: str
    marketing_version: str
    build_version: str
    platform: Literal["IOS"] = "IOS"


def validate_ipa(path: Path) -> AppleArtifactInfo: ...
```

- [x] Add test builders for XML and binary plists inside IPA ZIPs.
- [x] Write failing tests for valid IPA metadata/digest/name, wrong extension, empty/corrupt ZIP,
  traversal, missing/multiple top-level apps, invalid plist, and missing required keys.
- [x] Run the tests and verify the module is absent.
- [x] Implement safe ZIP/plist inspection without extraction and stream SHA-256.
- [x] Run package tests and confirm `Path.read_bytes` is never used.
- [x] Commit: `feat: validate Apple IPA artifacts`.

### Task 5: Safe Apple JSON:API client and target verification

**Files:**
- Create: `src/storehelper/stores/apple/errors.py`
- Create: `src/storehelper/stores/apple/client.py`
- Create: `src/storehelper/stores/apple/adapter.py`
- Create: `tests/integration/test_apple_verification.py`
- Create: `tests/unit/test_apple_errors.py`

**Interfaces:**

```python
class AppleClient:
    async def request_json(
        self, method: str, path: str, **kwargs: object
    ) -> Mapping[str, object]: ...
    async def verify_target(self, *, target: StoreTarget) -> VerifiedApplication: ...


def parse_apple_errors(payload: object) -> AppleVendorError: ...
```

- [x] Write failing tests for app read, version read with app relationship, version-string match,
  bundle-ID match, 401 refresh once, 403, 429/5xx bounded retry, redirect, malformed JSON:API,
  and raw-secret error bodies.
- [x] Run focused tests and capture expected failures.
- [x] Implement bearer-authenticated no-redirect requests and bounded sanitized errors.
- [x] Verify app/version identity before any upload request.
- [x] Run Apple verification and all Huawei/Harmony client tests.
- [x] Commit: `feat: verify Apple App Store targets`.

### Task 6: Build upload creation, range transfer, and commit

**Files:**
- Create: `src/storehelper/stores/apple/models.py`
- Modify: `src/storehelper/stores/apple/client.py`
- Modify: `src/storehelper/stores/apple/adapter.py`
- Create: `tests/unit/test_apple_upload_plan.py`
- Create: `tests/integration/test_apple_upload.py`

**Interfaces:**

```python
class AppleUploadOperation(BaseModel):
    url: SecretStr
    method: Literal["PUT"]
    offset: int
    length: int
    part_number: int
    headers: dict[str, SecretStr]
    entity_tag: SecretStr | None = None


def validate_upload_plan(
    operations: Sequence[Mapping[str, object]], file_size: int, now: datetime
) -> tuple[AppleUploadOperation, ...]: ...
```

- [x] Write failing plan tests for sorting, gap/overlap, duplicate parts, range overflow, expired
  operations, unsafe URLs, methods, headers, and already-delivered entity tags.
- [x] Write mocked integration tests for new reservation, exact-match reuse, ambiguous reuse,
  multiple streamed ranges, bearer-header isolation, redirect rejection, checksum commit, and
  interruption followed by reuse without a second build-upload POST.
- [x] Run tests and verify missing behavior.
- [x] Implement exact buildUpload/buildUploadFile JSON:API bodies from Apple's OpenAPI schema.
- [x] Implement bounded range readers and sequential no-redirect PUTs for undelivered operations.
- [x] Commit `uploaded=true` with SHA-256 and return only the build-upload ID.
- [x] Run focused and cross-store upload tests.
- [x] Commit: `feat: upload IPA builds through Apple delivery operations`.

### Task 7: Import processing and final Build ID transition

**Files:**
- Modify: `src/storehelper/stores/apple/adapter.py`
- Create: `tests/unit/test_apple_processing.py`
- Modify: `tests/integration/test_apple_upload.py`
- Modify: `tests/unit/test_publishing_service.py`

**Interfaces:**

```python
def parse_build_upload_status(payload: Mapping[str, object]) -> ProcessingStatus:
    # COMPLETE must return ProcessingStatus(READY, artifact_id=<Build ID>)
    ...
```

- [x] Write failing tests for AWAITING_UPLOAD, PROCESSING, FAILED state details, COMPLETE with Build
  ID, COMPLETE without Build ID, and unknown states.
- [x] Add a publisher test proving build-upload ID is atomically replaced by Build ID.
- [x] Run focused tests and verify failures.
- [x] Implement `GET /v1/buildUploads/{id}?include=build` parsing and safe state messages.
- [x] Run timeout/resume tests proving no file reservation or upload repeats after commit.
- [x] Commit: `feat: resolve Apple build processing`.

### Task 8: Build attachment and localized What's New

**Files:**
- Modify: `src/storehelper/stores/apple/client.py`
- Modify: `src/storehelper/stores/apple/adapter.py`
- Create: `tests/integration/test_apple_release_preparation.py`

**Interfaces:**

```python
async def prepare_release(
    *, target: StoreTarget, artifact_id: str, release_notes: str | None
) -> None: ...
```

- [x] Write failing tests for exact build relationship PATCH, notes omitted, unique locale lookup,
  locale missing/duplicate, 1/4000 boundaries, and a PATCH containing only `whatsNew`.
- [x] Run tests and verify no release preparation exists.
- [x] Implement build attachment first, then optional localized notes.
- [x] Preserve every unrelated version/localization field.
- [x] Run Apple release and Huawei/Harmony notes regression tests.
- [x] Commit: `feat: prepare Apple App Store versions`.

### Task 9: Review reconciliation, submission, and status

**Files:**
- Modify: `src/storehelper/stores/apple/client.py`
- Modify: `src/storehelper/stores/apple/adapter.py`
- Create: `tests/integration/test_apple_submission.py`
- Create: `tests/unit/test_apple_review_status.py`

**Interfaces:**

```python
async def submit(self, *, target: StoreTarget, artifact_id: str) -> str: ...
async def review_status(self, *, target: StoreTarget) -> ReviewStatus: ...
```

- [x] Write failing tests for new READY_FOR_REVIEW submission, existing draft reuse, existing item
  reuse, wrong-version item rejection, `submitted=true`, interruption after create/item, and
  409/422 safe rejection.
- [x] Write status mapping tests for READY/WAITING/IN_REVIEW/REJECTED/ACCEPTED/release states.
- [x] Run tests and verify failures.
- [x] Implement only `reviewSubmissions` and `reviewSubmissionItems`; do not call deprecated APIs.
- [x] Reconcile before every create so retries are idempotent.
- [x] Query configured App Store version status and normalize it.
- [x] Run all Apple adapter tests.
- [x] Commit: `feat: submit Apple versions for review`.

### Task 10: Runtime, credential CLI, and Apple commands

**Files:**
- Modify: `src/storehelper/stores/registry.py`
- Modify: `src/storehelper/runtime.py`
- Modify: `src/storehelper/cli.py`
- Modify: `tests/integration/test_cli_foundation.py`
- Modify: `tests/integration/test_cli_publish.py`
- Create: `tests/integration/test_apple_cli.py`

**Interfaces:**

```python
def build_runtime(
    application: ApplicationConfig,
    store: StoreName,
    credential: StoreCredential,
    http: httpx.AsyncClient,
) -> StoreRuntime: ...
```

- [x] Write failing CLI tests for `apple` help choices, credential import/verify, dry-run, no-submit,
  full JSON submit, prompt, timeout, resume-derived store, status, and wrong credential kind.
- [x] Run focused tests and verify the current CLI rejects Apple.
- [x] Add `--store` to credential import/delete/list semantics without breaking legacy Huawei use.
- [x] Resolve credential kind from the selected registry entry and construct the Apple runtime.
- [x] Keep noninteractive confirmation and output/exit-code contracts unchanged.
- [x] Run all CLI tests, Ruff, and mypy.
- [x] Commit: `feat: expose Apple publishing through the CLI`.

### Task 11: End-to-end recovery and security regression

**Files:**
- Create: `tests/integration/test_apple_publish_flow.py`
- Modify: `tests/unit/test_publishing_service.py`
- Modify: `tests/unit/test_run_repository.py`

**Acceptance scenarios:**

```text
valid IPA full submit
no-submit stops at processed Build
dry-run performs zero I/O
processing timeout and resume without upload
transfer interruption and exact reservation reuse
submission interruption and draft/item reconciliation
changed IPA rejection before resume network
v1/v2 receipt migration and v3 rewrite
no Apple secret or delivery value in output/error/receipt
```

- [ ] Write the mocked scenarios with injected clock/sleeper and known secret markers.
- [ ] Run the new file and record each gap.
- [ ] Apply only minimal production corrections.
- [ ] Run all unit/integration tests and scan non-test files for fixture secrets.
- [ ] Commit: `test: cover Apple publishing recovery and security`.

### Task 12: Documentation, versioning, and release checklist

**Files:**
- Modify: `README.md`
- Modify: `docs/SECURITY_GUIDE.md`
- Modify: `docs/DEVELOPMENT_PLAN.md`
- Create: `docs/APPLE_MANUAL_TEST.md`
- Modify: `examples/storehelper.yaml`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/storehelper/__init__.py`
- Modify: version test expectations

- [ ] Document native API prerequisites, team/individual credentials, existing app/version IDs,
  IPA commands, optional notes, no-submit, recovery, status, JSON, and security boundaries.
- [ ] Add a live checklist ordered read-only verify → dry-run → no-submit → separately confirmed review.
- [ ] Verify every command against installed help and keep all examples fake.
- [ ] Bump package version to `0.3.0` consistently.
- [ ] Mark Apple tasks complete only after their verification succeeds.
- [ ] Commit: `docs: document Apple App Store publishing`.

### Task 13: Full verification and milestone closure

**Files:**
- Modify only files required by fresh verification findings.
- Modify: `docs/DEVELOPMENT_PLAN.md` after every gate passes.

- [ ] Run `ruff format --check .` and `ruff check .`.
- [ ] Run `mypy src`.
- [ ] Run `python -m pytest --cov=storehelper --cov-report=term-missing --cov-fail-under=90`.
- [ ] Build sdist/wheel in a fresh temporary directory and run Twine checks.
- [ ] Install the exact wheel in a fresh isolated environment.
- [ ] Run installed help/version, three-store config validation, and Huawei/HarmonyOS/Apple dry-runs.
- [ ] Run `git diff --check`, generated-artifact checks, and complete private-data/secret review.
- [ ] Mark the Apple milestone complete only after all fresh commands exit zero.
- [ ] Commit: `chore: complete Apple milestone verification`.

## Completion rule

The Apple increment is complete only when Tasks 1–13 are checked, all Huawei and HarmonyOS
regressions remain green, the exact v0.3.0 wheel passes installed dry-runs, and the development
plan marks Apple complete. Automated verification never authorizes a live Apple upload or review
submission.
