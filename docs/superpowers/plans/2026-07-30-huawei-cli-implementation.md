# Huawei CLI v0.1.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a tested, secure, local-first Python CLI that publishes APK/AAB packages end to end to an existing Huawei AppGallery Android application and can resume timed-out or interrupted runs.

**Architecture:** A modular Python package exposes an async `Publisher` application service used by both Typer commands and future integrations. Configuration, credentials, run storage, and Huawei HTTP behavior are isolated behind focused interfaces; the Huawei adapter is the only vendor implementation in v0.1.0.

**Tech Stack:** Python 3.11+, Hatchling, Typer, Rich, httpx, Pydantic, PyYAML, cryptography, keyring, platformdirs, pytest, pytest-asyncio, respx, Ruff, mypy.

## Global Constraints

- Support Python 3.11 through 3.14.
- Support only Huawei AppGallery Android Publishing API v2 and existing applications.
- Accept only APK and AAB packages up to 4 GiB.
- Support only Huawei Service Account authentication with `key_id`, `sub_account`, and `private_key`.
- Never store or emit private keys, JWTs, authorization headers, auth codes, upload URLs, destination URLs, or raw Huawei bodies.
- Configuration is strict schema version 1 and may not contain secret fields.
- JSON mode emits exactly one result document to stdout; progress and diagnostics use stderr.
- All production behavior is introduced with a failing behavioral test first.
- Do not call the live Huawei API from automated tests.
- Keep `docs/DEVELOPMENT_PLAN.md` synchronized after every completed task.

---

## File map

```text
pyproject.toml                                  package, tools, entry point
src/storehelper/__init__.py                    public API exports
src/storehelper/cli.py                         Typer app and top-level exit boundary
src/storehelper/domain/models.py               typed results, stages, publish request
src/storehelper/domain/errors.py               stable error classes and redaction
src/storehelper/domain/exit_codes.py            process exit mapping
src/storehelper/config/models.py               strict schema version 1 models
src/storehelper/config/loader.py               YAML discovery, loading, selection
src/storehelper/credentials/models.py          Service Account value model
src/storehelper/credentials/providers.py       environment/keyring/prompt resolution
src/storehelper/credentials/service.py         import, list, verify, delete operations
src/storehelper/stores/base.py                 adapter protocol
src/storehelper/stores/huawei/auth.py          PEM normalization and PS256 JWT
src/storehelper/stores/huawei/errors.py        Huawei response parsing and translations
src/storehelper/stores/huawei/package.py       validation, hash, logical name
src/storehelper/stores/huawei/client.py        sanitized HTTP boundary
src/storehelper/stores/huawei/adapter.py       store-facing Huawei operations
src/storehelper/runs/models.py                 schema version 1 run receipt
src/storehelper/runs/repository.py             platform path and atomic persistence
src/storehelper/publishing/service.py          publish/resume/status orchestration
src/storehelper/output/renderers.py            text and JSON output
src/storehelper/commands/*.py                  command-specific argument handling
tests/unit/                                    pure behavior tests
tests/integration/                             HTTP and CLI boundary tests
.github/workflows/ci.yml                       quality matrix
.github/workflows/release.yml                  PyPI Trusted Publishing and GitHub release
README.md                                      user documentation
examples/storehelper.yaml                      redacted configuration example
```

### Task 1: Package foundation and domain contract

**Files:**
- Create: `pyproject.toml`
- Create: `src/storehelper/__init__.py`
- Create: `src/storehelper/domain/models.py`
- Create: `src/storehelper/domain/errors.py`
- Create: `src/storehelper/domain/exit_codes.py`
- Create: `tests/unit/test_domain.py`
- Modify: `.gitignore`
- Modify: `docs/DEVELOPMENT_PLAN.md`

**Interfaces:**
- Produces `PublishStage`, `PublishRequest`, `OperationResult`, `VendorError`, and `NextAction` Pydantic models.
- Produces `StoreHelperError(code, message, exit_code, resumable=False, vendor_code=None)`.
- Produces `redact(value: str) -> str` and `exit_code_for(error) -> int`.

- [ ] **Step 1: Write failing domain and redaction tests**

```python
def test_operation_result_serializes_stable_schema():
    result = OperationResult.success(stage=PublishStage.SUBMITTED, run_id="run-1")
    assert result.model_dump(mode="json") == {
        "schema_version": 1,
        "ok": True,
        "run_id": "run-1",
        "store": "huawei",
        "stage": "submitted",
        "resumable": False,
        "message": "Huawei accepted the review submission.",
        "vendor": None,
        "next_action": None,
    }

def test_redact_removes_pem_jwt_and_authorization():
    value = "Authorization: Bearer aaa.bbb.ccc\\n-----BEGIN PRIVATE KEY-----\\nsecret\\n-----END PRIVATE KEY-----"
    redacted = redact(value)
    assert "aaa.bbb.ccc" not in redacted
    assert "secret" not in redacted
    assert "[REDACTED]" in redacted
```

- [ ] **Step 2: Run the focused test and verify import failure**

Run: `python -m pytest tests/unit/test_domain.py -q`  
Expected: FAIL because `storehelper.domain` does not exist.

- [ ] **Step 3: Add packaging metadata and minimal domain implementation**

Configure Hatchling with `src` layout, console script `storehelper = "storehelper.cli:main"`, Python `>=3.11`, Apache-2.0 metadata, runtime dependencies from the global stack, and a `dev` extra. Implement string-valued stages, schema version 1 result factories, stable errors, and redaction for PEM blocks, bearer values, JWT-shaped values, `authCode`, and known credential fields.

- [ ] **Step 4: Run domain tests and quality tools**

Run: `python -m pytest tests/unit/test_domain.py -q`  
Expected: PASS.  
Run: `ruff format . && ruff check .`  
Expected: exit 0.

- [ ] **Step 5: Mark project foundation progress and commit**

```bash
git add pyproject.toml .gitignore src tests/unit/test_domain.py docs/DEVELOPMENT_PLAN.md
git commit -m "feat: establish StoreHelper package contracts"
```

### Task 2: Strict multi-application configuration

**Files:**
- Create: `src/storehelper/config/__init__.py`
- Create: `src/storehelper/config/models.py`
- Create: `src/storehelper/config/loader.py`
- Create: `tests/unit/test_config.py`
- Modify: `docs/DEVELOPMENT_PLAN.md`

**Interfaces:**
- Produces `StoreHelperConfig`, `ApplicationConfig`, `HuaweiStoreConfig`.
- Produces `load_config(path: Path) -> StoreHelperConfig`.
- Produces `select_application(config, alias: str | None) -> tuple[str, ApplicationConfig]`.
- Produces `write_example_config(path: Path) -> None` without overwrite.

- [ ] **Step 1: Write failing tests for strict parsing, selection, and secret rejection**

```python
def test_loads_one_huawei_application(tmp_path):
    path = tmp_path / "storehelper.yaml"
    path.write_text("""version: 1
apps:
  wallet:
    package_name: com.example.wallet
    stores:
      huawei:
        app_id: '123'
        credential_profile: company
""")
    config = load_config(path)
    alias, app = select_application(config, None)
    assert alias == "wallet"
    assert app.stores.huawei.language == "zh-CN"

@pytest.mark.parametrize("secret_key", ["private_key", "client_secret", "access_token", "password"])
def test_rejects_secret_fields_anywhere(tmp_path, secret_key):
    path = write_config(tmp_path, extra=f"        {secret_key}: leaked")
    with pytest.raises(ConfigError, match="Secrets are not allowed"):
        load_config(path)
```

- [ ] **Step 2: Run tests and verify they fail because config modules are absent**

Run: `python -m pytest tests/unit/test_config.py -q`  
Expected: FAIL on missing imports.

- [ ] **Step 3: Implement strict Pydantic models and YAML loader**

Use `ConfigDict(extra="forbid")`, validate version exactly `1`, aliases with `^[a-z0-9][a-z0-9_-]*$`, non-empty strings, and recursively reject secret-key names before model validation. Do not search parent directories.

- [ ] **Step 4: Implement safe example generation and application selection**

Writing an existing path raises `CONFIG_EXISTS`. Omitted alias auto-selects exactly one app; zero or multiple apps raise `APP_SELECTION_REQUIRED`; unknown alias raises `APP_NOT_FOUND`.

- [ ] **Step 5: Run focused and full tests**

Run: `python -m pytest tests/unit/test_config.py -q && python -m pytest -q`  
Expected: all tests PASS.

- [ ] **Step 6: Update progress and commit**

```bash
git add src/storehelper/config tests/unit/test_config.py docs/DEVELOPMENT_PLAN.md
git commit -m "feat: add strict project configuration"
```

### Task 3: Secure Huawei Service Account credentials

**Files:**
- Create: `src/storehelper/credentials/__init__.py`
- Create: `src/storehelper/credentials/models.py`
- Create: `src/storehelper/credentials/providers.py`
- Create: `src/storehelper/credentials/service.py`
- Create: `tests/unit/test_credentials.py`
- Modify: `docs/DEVELOPMENT_PLAN.md`

**Interfaces:**
- Produces `HuaweiServiceAccount(key_id, sub_account, private_key, token_uri)` with redacted repr.
- Produces `CredentialProvider.resolve(profile: str, interactive: bool) -> HuaweiServiceAccount`.
- Produces `CredentialService.import_file`, `list_profiles`, `verify`, and `delete`.
- Accepts injected `KeyringBackend` and `PromptBackend` protocols for tests.

- [ ] **Step 1: Write failing precedence and leakage tests**

```python
def test_environment_credentials_win_over_keyring(monkeypatch, fake_keyring):
    set_service_account_env(monkeypatch, key_id="env-kid")
    fake_keyring.set("company", service_account_json(key_id="keyring-kid"))
    resolved = CredentialProvider(fake_keyring).resolve("company", interactive=False)
    assert resolved.key_id == "env-kid"

def test_service_account_repr_never_contains_private_key():
    account = valid_account(private_key="super-secret-private-key")
    assert "super-secret-private-key" not in repr(account)
```

- [ ] **Step 2: Run the focused test and verify missing implementation failure**

Run: `python -m pytest tests/unit/test_credentials.py -q`  
Expected: FAIL on missing credential modules.

- [ ] **Step 3: Implement credential models and environment loading**

Support either the three individual environment values or `STOREHELPER_HUAWEI_CREDENTIALS_FILE`, reject both forms together, normalize literal newlines, validate an unencrypted PEM with `cryptography`, and exclude secret fields from repr and validation messages.

- [ ] **Step 4: Implement keyring and prompt providers**

Store one JSON string under service `storehelper:huawei` and username/profile. Raise `KEYRING_UNAVAILABLE` on missing or fail backends. Prompt `key_id` and `sub_account` normally and collect the private key without echo; never persist unless the explicit import command is used.

- [ ] **Step 5: Implement credential service operations**

Import an explicitly selected JSON file, list only profile names maintained in a non-secret keyring index, verify through an injected async verifier, and require a caller-provided confirmation decision for deletion.

- [ ] **Step 6: Run focused and full tests**

Run: `python -m pytest tests/unit/test_credentials.py -q && python -m pytest -q`  
Expected: all tests PASS and captured output excludes fixture secrets.

- [ ] **Step 7: Update progress and commit**

```bash
git add src/storehelper/credentials tests/unit/test_credentials.py docs/DEVELOPMENT_PLAN.md
git commit -m "feat: secure Huawei service account credentials"
```

### Task 4: APK/AAB validation and logical file naming

**Files:**
- Create: `src/storehelper/stores/__init__.py`
- Create: `src/storehelper/stores/huawei/__init__.py`
- Create: `src/storehelper/stores/huawei/package.py`
- Create: `tests/unit/test_huawei_package.py`
- Modify: `docs/DEVELOPMENT_PLAN.md`

**Interfaces:**
- Produces `PackageInfo(path, kind, size, sha256, logical_name, warnings)`.
- Produces `validate_package(path: Path) -> PackageInfo`.
- Produces `logical_package_name(name: str) -> str`.

- [ ] **Step 1: Write failing table-driven filename and package tests**

```python
@pytest.mark.parametrize(("source", "expected"), [
    ("1785240000000-12ab34cd-app-release.apk", "app-release.apk"),
    ("../../bad\\x00name.aab", "badname.aab"),
])
def test_logical_package_name(source, expected):
    assert logical_package_name(source) == expected

def test_aab_requires_bundle_and_base_manifest(tmp_path):
    path = make_zip(tmp_path / "app.aab", ["BundleConfig.pb"])
    with pytest.raises(PackageError, match="base/manifest/AndroidManifest.xml"):
        validate_package(path)
```

- [ ] **Step 2: Run tests and verify missing implementation failure**

Run: `python -m pytest tests/unit/test_huawei_package.py -q`  
Expected: FAIL on missing module.

- [ ] **Step 3: Implement deterministic safe naming**

Strip directory components, control characters, path separators, and the known `<13 digits>-<8 hex>-` storage prefix. Normalize whitespace to dashes, retain only a safe readable stem, preserve suffix, and truncate by Unicode code point to 64 characters.

- [ ] **Step 4: Implement streaming validation and hash calculation**

Use `zipfile.ZipFile.testzip`, enforce the documented entries, read SHA-256 in 1 MiB chunks, reject non-file, unreadable, empty, unsupported, invalid ZIP, and size-over-limit inputs with `PackageError` exit 4.

- [ ] **Step 5: Run focused and full tests**

Run: `python -m pytest tests/unit/test_huawei_package.py -q && python -m pytest -q`  
Expected: all tests PASS.

- [ ] **Step 6: Update progress and commit**

```bash
git add src/storehelper/stores tests/unit/test_huawei_package.py docs/DEVELOPMENT_PLAN.md
git commit -m "feat: validate Huawei Android packages"
```

### Task 5: PS256 authentication and Huawei error mapping

**Files:**
- Create: `src/storehelper/stores/huawei/auth.py`
- Create: `src/storehelper/stores/huawei/errors.py`
- Create: `tests/unit/test_huawei_auth.py`
- Create: `tests/unit/test_huawei_errors.py`
- Modify: `docs/DEVELOPMENT_PLAN.md`

**Interfaces:**
- Produces `HuaweiAuth.headers(now: datetime | None = None, force_refresh=False) -> dict[str, str]`.
- Produces `parse_huawei_response(data: Mapping) -> HuaweiResponseStatus`.
- Produces `translate_huawei_error(code: str, message: str | None) -> HuaweiVendorError`.

- [ ] **Step 1: Write failing JWT tests with a generated RSA fixture key**

```python
def test_jwt_uses_ps256_expected_claims_and_key_id(rsa_private_key):
    account = valid_account(private_key=rsa_private_key, key_id="kid-1", sub_account="sub-1")
    token = HuaweiAuth(account).token(now=datetime(2026, 7, 30, tzinfo=timezone.utc))
    header, payload = decode_without_verification(token)
    assert header == {"alg": "PS256", "kid": "kid-1", "typ": "JWT"}
    assert payload["iss"] == "sub-1"
    assert payload["exp"] - payload["iat"] == 3600
```

- [ ] **Step 2: Write failing error parsing tests**

```python
@pytest.mark.parametrize(("payload", "code"), [
    ({"ret": {"code": 204144727, "msg": "compiling"}}, "204144727"),
    ({"ret": "{\\"code\\":204144662,\\"msg\\":\\"bad name\\"}"}, "204144662"),
])
def test_parses_object_and_string_ret(payload, code):
    assert parse_huawei_response(payload).code == code
```

- [ ] **Step 3: Run tests and verify missing implementation failures**

Run: `python -m pytest tests/unit/test_huawei_auth.py tests/unit/test_huawei_errors.py -q`  
Expected: FAIL on missing modules.

- [ ] **Step 4: Implement PS256 signing and in-memory renewal**

Use `cryptography` with PSS salt length `hashes.SHA256.digest_size`, deterministic compact JSON, base64url without padding, one-hour lifetime, 60-second renewal margin, and headers containing bearer token plus `client_id=key_id`.

- [ ] **Step 5: Implement safe Huawei error parsing and translations**

Parse object or JSON-string `ret`, normalize numeric codes to strings, translate the documented high-value codes, bound messages to 500 safe characters, and classify compiling, authentication, transient, and fatal outcomes.

- [ ] **Step 6: Run focused and full tests**

Run: `python -m pytest tests/unit/test_huawei_auth.py tests/unit/test_huawei_errors.py -q && python -m pytest -q`  
Expected: all tests PASS.

- [ ] **Step 7: Update progress and commit**

```bash
git add src/storehelper/stores/huawei tests/unit/test_huawei_auth.py tests/unit/test_huawei_errors.py docs/DEVELOPMENT_PLAN.md
git commit -m "feat: authenticate Huawei service accounts"
```

### Task 6: Sanitized Huawei HTTP client for upload and binding

**Files:**
- Create: `src/storehelper/stores/huawei/models.py`
- Create: `src/storehelper/stores/huawei/client.py`
- Create: `tests/integration/test_huawei_upload.py`
- Modify: `docs/DEVELOPMENT_PLAN.md`

**Interfaces:**
- Produces `HuaweiClient.verify_app`, `request_upload`, `upload_file`, and `bind_package`.
- Produces public-safe `HuaweiApp`, `UploadTicket` internal secret type, and `BoundPackage(pkg_version)`.
- Accepts injected `httpx.AsyncClient` and `HuaweiAuth`.

- [ ] **Step 1: Write failing contract test for verification, upload, and binding**

```python
@pytest.mark.asyncio
async def test_upload_and_bind_uses_logical_name_and_returns_pkg_version(huawei_mock, package_info):
    huawei_mock.add_complete_upload_flow(pkg_version="10004151")
    client = make_client(huawei_mock)
    bound = await client.upload_and_bind(app_id="123", package=package_info)
    assert bound.pkg_version == "10004151"
    assert huawei_mock.multipart_filename == package_info.logical_name
    assert huawei_mock.binding_json == {
        "fileType": 5,
        "files": [{"fileName": package_info.logical_name, "fileDestUrl": huawei_mock.destination}],
    }
```

- [ ] **Step 2: Run test and verify missing client failure**

Run: `python -m pytest tests/integration/test_huawei_upload.py -q`  
Expected: FAIL because `HuaweiClient` does not exist.

- [ ] **Step 3: Implement authenticated request boundary**

Use one injected `httpx.AsyncClient`, explicit timeouts, TLS verification, disabled authenticated redirects, safe status parsing, one forced JWT renewal for 401/403, and exceptions that never embed raw request headers or full bodies.

- [ ] **Step 4: Implement streamed upload and binding**

Parse upload response variants (`fileDestUlr` and `fileDestUrl`), keep ticket values in an internal redacted model, use the same logical filename for multipart and binding, require exactly one non-empty `pkgVersion`, and return only that public-safe identifier.

- [ ] **Step 5: Add failure contract tests**

Cover missing `authCode`, malformed upload response, Huawei `204144662`, multiple/missing `pkgVersion`, HTTP redirect downgrade, and assert fixture secret strings never appear in errors.

- [ ] **Step 6: Run focused and full tests**

Run: `python -m pytest tests/integration/test_huawei_upload.py -q && python -m pytest -q`  
Expected: all tests PASS.

- [ ] **Step 7: Update progress and commit**

```bash
git add src/storehelper/stores/huawei tests/integration/test_huawei_upload.py docs/DEVELOPMENT_PLAN.md
git commit -m "feat: upload and bind Huawei packages"
```

### Task 7: Huawei compile polling, metadata, submission, and status

**Files:**
- Create: `src/storehelper/stores/base.py`
- Create: `src/storehelper/stores/huawei/adapter.py`
- Create: `tests/unit/test_huawei_compile.py`
- Create: `tests/integration/test_huawei_submission.py`
- Modify: `docs/DEVELOPMENT_PLAN.md`

**Interfaces:**
- Produces `StoreAdapter` async protocol.
- Produces `HuaweiAndroidAdapter.verify`, `upload`, `compile_status`, `update_release_notes`, `submit`, and `review_status`.
- Produces `CompileState` values `processing`, `ready`, and `failed`.

- [ ] **Step 1: Write failing compile-state table tests**

```python
@pytest.mark.parametrize(("success_status", "expected"), [
    (0, CompileState.READY),
    (1, CompileState.PROCESSING),
    (2, CompileState.FAILED),
])
def test_maps_documented_compile_status(success_status, expected):
    payload = {"ret": {"code": 0}, "pkgStateList": [{"pkgId": "42", "successStatus": success_status}]}
    assert parse_compile_status(payload, "42").state is expected
```

- [ ] **Step 2: Run compile test and verify missing implementation failure**

Run: `python -m pytest tests/unit/test_huawei_compile.py -q`  
Expected: FAIL on missing parser.

- [ ] **Step 3: Implement compile parser and adapter protocol**

Match by `pkgId`, treat missing records as processing, implement documented `successStatus`, support legacy `aabCompileStatus`, preserve only safe failure reason codes, and reject unknown terminal statuses.

- [ ] **Step 4: Write failing submission contract tests**

Verify `PUT /app-language-info` contains only `lang` and `newFeatures`, `POST /app-submit` uses `releaseType=1`, `204144727` is classified as compiling, and `GET /app-info` maps Huawei review states to normalized safe strings.

- [ ] **Step 5: Implement metadata, submit, status, and adapter façade**

Keep metadata updates minimal, enforce 1-500 characters, return a safe app ID as submission ID, and expose typed adapter results without raw HTTP details.

- [ ] **Step 6: Run focused and full tests**

Run: `python -m pytest tests/unit/test_huawei_compile.py tests/integration/test_huawei_submission.py -q && python -m pytest -q`  
Expected: all tests PASS.

- [ ] **Step 7: Update progress and commit**

```bash
git add src/storehelper/stores tests/unit/test_huawei_compile.py tests/integration/test_huawei_submission.py docs/DEVELOPMENT_PLAN.md
git commit -m "feat: submit Huawei packages for review"
```

### Task 8: Atomic redacted run receipts

**Files:**
- Create: `src/storehelper/runs/__init__.py`
- Create: `src/storehelper/runs/models.py`
- Create: `src/storehelper/runs/repository.py`
- Create: `tests/unit/test_run_repository.py`
- Modify: `docs/DEVELOPMENT_PLAN.md`

**Interfaces:**
- Produces `RunReceipt` schema version 1 and `RunState`.
- Produces `RunRepository.create`, `save`, `get`, `list`, `delete`, and `find_resumable`.
- Accepts a root `Path` override for deterministic tests.

- [ ] **Step 1: Write failing atomic persistence and redaction tests**

```python
def test_receipt_round_trip_and_duplicate_lookup(tmp_path):
    repo = RunRepository(tmp_path)
    receipt = sample_receipt(app_id="123", sha256="abc", state=RunState.PACKAGE_COMPILING)
    repo.save(receipt)
    assert repo.get(receipt.run_id) == receipt
    assert repo.find_resumable("huawei", "123", "abc").run_id == receipt.run_id

def test_receipt_rejects_secret_fields():
    with pytest.raises(ValidationError):
        RunReceipt.model_validate({**sample_receipt_dict(), "access_token": "secret"})
```

- [ ] **Step 2: Run tests and verify missing repository failure**

Run: `python -m pytest tests/unit/test_run_repository.py -q`  
Expected: FAIL on missing run modules.

- [ ] **Step 3: Implement strict receipt model and transitions**

Reject extra fields and unsupported schema versions. Define allowed resumable and terminal states and safe next-state transitions. Generate sortable run IDs from UTC timestamp plus random hexadecimal suffix.

- [ ] **Step 4: Implement atomic persistence and platform directory selection**

Use `platformdirs.user_state_path`, owner-only directories/files where supported, temp file in the same directory, flush, `os.fsync`, and `os.replace`. Quarantine malformed receipts by raising `STATE_CORRUPT` rather than deleting them.

- [ ] **Step 5: Run focused and full tests**

Run: `python -m pytest tests/unit/test_run_repository.py -q && python -m pytest -q`  
Expected: all tests PASS.

- [ ] **Step 6: Update progress and commit**

```bash
git add src/storehelper/runs tests/unit/test_run_repository.py docs/DEVELOPMENT_PLAN.md
git commit -m "feat: persist redacted publishing runs"
```

### Task 9: Publishing orchestration, polling, and resume

**Files:**
- Create: `src/storehelper/publishing/__init__.py`
- Create: `src/storehelper/publishing/service.py`
- Create: `tests/unit/test_publishing_service.py`
- Modify: `src/storehelper/__init__.py`
- Modify: `docs/DEVELOPMENT_PLAN.md`

**Interfaces:**
- Produces public `Publisher.publish(request)`, `resume(run_id)`, and `status(app_alias, store)` async methods.
- Accepts `StoreAdapter`, `CredentialProvider`, `RunRepository`, monotonic clock, and async sleeper.

- [ ] **Step 1: Write failing happy-path state transition test**

```python
@pytest.mark.asyncio
async def test_publish_uploads_waits_updates_and_submits(tmp_path):
    adapter = FakeAdapter(compile_states=[PROCESSING, READY])
    publisher = make_publisher(tmp_path, adapter)
    result = await publisher.publish(sample_request(submit=True, confirmed=True))
    assert result.ok is True
    assert result.stage is PublishStage.SUBMITTED
    assert adapter.calls == ["verify", "upload", "compile", "compile", "notes", "submit"]
```

- [ ] **Step 2: Run test and verify missing service failure**

Run: `python -m pytest tests/unit/test_publishing_service.py::test_publish_uploads_waits_updates_and_submits -q`  
Expected: FAIL on missing `Publisher`.

- [ ] **Step 3: Implement happy path, dry-run, and no-submit**

Persist after each durable transition, never persist transient upload ticket data, place release-note update after package readiness, and return schema version 1 results.

- [ ] **Step 4: Write failing retry, timeout, interruption, duplicate, and resume tests**

Cover HTTP transient result, 204144727 after ready, 10-minute deadline with injected clock, cancellation mapping to interrupted, existing same-hash bound run detection, and resume starting at compilation without calling upload.

- [ ] **Step 5: Implement bounded polling and resume table**

Use the injected sleeper and monotonic clock, honor safe retry delay, refresh auth through the adapter once, return exit 6-compatible timeout results, and map each receipt state to the earliest safe next operation.

- [ ] **Step 6: Run focused and full tests**

Run: `python -m pytest tests/unit/test_publishing_service.py -q && python -m pytest -q`  
Expected: all tests PASS.

- [ ] **Step 7: Export public API, update progress, and commit**

```bash
git add src/storehelper/publishing src/storehelper/__init__.py tests/unit/test_publishing_service.py docs/DEVELOPMENT_PLAN.md
git commit -m "feat: orchestrate resumable Huawei publishing"
```

### Task 10: Renderers and foundational CLI commands

**Files:**
- Create: `src/storehelper/output/__init__.py`
- Create: `src/storehelper/output/renderers.py`
- Create: `src/storehelper/commands/__init__.py`
- Create: `src/storehelper/commands/config.py`
- Create: `src/storehelper/commands/credentials.py`
- Create: `src/storehelper/cli.py`
- Create: `tests/unit/test_renderers.py`
- Create: `tests/integration/test_cli_foundation.py`
- Modify: `docs/DEVELOPMENT_PLAN.md`

**Interfaces:**
- Produces `render_result(result, output, stdout, stderr) -> None`.
- Produces a Typer app and import-safe `main() -> None`.

- [ ] **Step 1: Write failing renderer tests**

```python
def test_json_renderer_writes_only_one_document_to_stdout(capsys):
    render_result(sample_timeout_result(), output="json")
    captured = capsys.readouterr()
    payload, end = json.JSONDecoder().raw_decode(captured.out)
    assert payload["schema_version"] == 1
    assert captured.out[end:].strip() == ""
    assert "private-key-fixture" not in captured.out + captured.err
```

- [ ] **Step 2: Run renderer test and verify missing implementation failure**

Run: `python -m pytest tests/unit/test_renderers.py -q`  
Expected: FAIL on missing output module.

- [ ] **Step 3: Implement text and JSON renderers**

Serialize with Pydantic JSON mode, ensure a trailing newline, send diagnostics to stderr, format text with Rich without changing semantic result fields, and reapply redaction at the output boundary.

- [ ] **Step 4: Write failing CLI tests for help, init, config, and credentials**

Use `CliRunner` to assert `--help`, `version`, non-overwriting `init`, strict `config validate`, credential import with fake keyring, non-secret listing, verify, and confirmed deletion.

- [ ] **Step 5: Implement Typer application and foundational commands**

Build commands from injected service factories, catch only typed `StoreHelperError` at the top boundary, print safe result/error output, and exit using the documented code table.

- [ ] **Step 6: Run focused and full tests**

Run: `python -m pytest tests/unit/test_renderers.py tests/integration/test_cli_foundation.py -q && python -m pytest -q`  
Expected: all tests PASS.

- [ ] **Step 7: Update progress and commit**

```bash
git add src/storehelper/output src/storehelper/commands src/storehelper/cli.py tests docs/DEVELOPMENT_PLAN.md
git commit -m "feat: add secure CLI foundations"
```

### Task 11: Publish, resume, status, and run commands

**Files:**
- Create: `src/storehelper/commands/publish.py`
- Create: `src/storehelper/commands/runs.py`
- Create: `tests/integration/test_cli_publish.py`
- Modify: `src/storehelper/cli.py`
- Modify: `docs/DEVELOPMENT_PLAN.md`

**Interfaces:**
- Registers `publish`, `resume`, `status`, and `runs list|show|delete`.
- Uses only public `Publisher`, `RunRepository`, and renderer interfaces.

- [ ] **Step 1: Write failing CLI happy-path and safety tests**

```python
def test_noninteractive_submit_requires_yes(cli_runner, app):
    result = cli_runner.invoke(app, ["publish", "--app", "wallet", "--store", "huawei", "--file", "app.aab"])
    assert result.exit_code == 2
    assert "--yes" in result.stderr

def test_json_publish_stdout_is_parseable(cli_runner, app, fake_publisher):
    result = cli_runner.invoke(app, publish_args("--yes", "--output", "json"))
    assert result.exit_code == 0
    assert json.loads(result.stdout)["stage"] == "submitted"
```

- [ ] **Step 2: Run CLI publish tests and verify missing command failure**

Run: `python -m pytest tests/integration/test_cli_publish.py -q`  
Expected: FAIL because publishing commands are not registered.

- [ ] **Step 3: Implement publish input validation and confirmation**

Enforce mutually exclusive modes and release-note sources, parse durations, require `--yes` in non-interactive full submit, show the confirmation plan in text mode, and construct a typed `PublishRequest`.

- [ ] **Step 4: Implement resume, status, and run management commands**

Render all outcomes through the shared renderer, do not prompt in JSON mode, require confirmation for deletion, and keep run JSON fields redacted.

- [ ] **Step 5: Add full CLI scenario tests**

Cover rejection of confirmation, dry-run without credentials, no-submit without notes, timeout exit 6 with next command, interrupted receipt, resume without upload, status query, corrupted receipt exit 7, and all documented output modes.

- [ ] **Step 6: Run focused and full tests**

Run: `python -m pytest tests/integration/test_cli_publish.py -q && python -m pytest -q`  
Expected: all tests PASS.

- [ ] **Step 7: Update progress and commit**

```bash
git add src/storehelper/commands src/storehelper/cli.py tests/integration/test_cli_publish.py docs/DEVELOPMENT_PLAN.md
git commit -m "feat: expose end-to-end Huawei CLI"
```

### Task 12: CI, documentation, build, and installed CLI verification

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/release.yml`
- Create: `examples/storehelper.yaml`
- Create: `examples/RELEASE_NOTES.md`
- Create: `docs/SECURITY_GUIDE.md`
- Create: `docs/RELEASING.md`
- Replace: `README.md`
- Modify: `docs/DEVELOPMENT_PLAN.md`

**Interfaces:**
- CI runs format, lint, mypy, tests with 90% coverage, and build.
- Release workflow uses GitHub environment `pypi`, OIDC `id-token: write`, and immutable tags.

- [ ] **Step 1: Add CI workflow and run every command locally**

Commands:

```bash
ruff format --check .
ruff check .
mypy src
python -m pytest --cov=storehelper --cov-report=term-missing --cov-fail-under=90
python -m build
```

Expected: every command exits 0 before the workflow is committed.

- [ ] **Step 2: Add release workflow with a non-publishing build job**

Build wheel and sdist, verify with `twine check`, upload artifacts, and make the PyPI publish job depend on a `v*` tag plus protected `pypi` environment. Do not configure a real PyPI project or publish during this task.

- [ ] **Step 3: Write user and maintainer documentation**

Document pipx install, source install, init, Service Account import, CI secrets, publish/no-submit/dry-run, timeout/resume, JSON schema, exit codes, security limitations, troubleshooting for known Huawei codes, and the release process. Examples contain no real identifiers or secrets.

- [ ] **Step 4: Build and install the wheel into a disposable pipx environment**

```bash
python -m build
pipx install --force dist/storehelper-*.whl
storehelper version
storehelper --help
pipx uninstall storehelper
```

Expected: installed command reports the package version and help without importing the source checkout.

- [ ] **Step 5: Run final security searches and full verification**

```bash
rg -n "BEGIN (RSA )?PRIVATE KEY|access_token|authCode|Authorization: Bearer" . --glob '!docs/superpowers/**' --glob '!tests/**'
ruff format --check .
ruff check .
mypy src
python -m pytest --cov=storehelper --cov-report=term-missing --cov-fail-under=90
python -m build
git diff --check
```

Expected: security search finds only intentional redaction/config-rejection identifiers, all quality commands exit 0, and no secret fixture value occurs outside tests.

- [ ] **Step 6: Complete progress checklist and commit**

```bash
git add .github README.md examples docs pyproject.toml src tests
git commit -m "docs: prepare StoreHelper CLI release"
```

- [ ] **Step 7: Review branch history and working tree**

Run: `git status --short --branch && git log --oneline --decorate main..HEAD`  
Expected: clean working tree with focused commits for documentation, foundation, configuration, credentials, package validation, authentication, upload, submission, persistence, orchestration, CLI, and release preparation.
