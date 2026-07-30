# StoreHelper Huawei CLI Design

**Status:** Approved for implementation  
**Date:** 2026-07-30  
**Target release:** v0.1.0

## 1. Purpose

StoreHelper v0.1.0 is a local-first Python CLI and reusable Python package that publishes an APK or AAB to an existing Huawei AppGallery Connect Android application. It validates local input, authenticates with a Huawei Service Account, uploads and binds the package, waits for compilation, updates release notes, submits the application for review, records a redacted local receipt, and supports resuming an interrupted or timed-out run.

The implementation is original open-source code guided by public Huawei protocol documentation and observed behavior in the owner's private MVP and production projects. It must not copy private product infrastructure, organization-specific configuration, credentials, endpoints, or business data.

## 2. Goals

- Support Python 3.11 and later.
- Support Huawei Android Publishing API v2 for `.apk` and `.aab` files.
- Publish only to applications that already exist in AppGallery Connect.
- Support one configuration file containing one or more named applications.
- Use Huawei Service Account credentials: `key_id`, `sub_account`, and `private_key`.
- Keep credentials out of command history, project configuration, logs, run receipts, and machine output.
- Support interactive local use and non-interactive CI/CD use.
- Provide stable text and JSON output.
- Avoid duplicate package uploads when a bound package can be resumed safely.
- Require no database, Redis server, hosted StoreHelper service, or application-binding service.
- Expose the publishing behavior as a reusable async Python API for the future MCP server.

## 3. Non-goals

- HarmonyOS `.app` or `.hap` publishing.
- Creating applications or developer accounts in AppGallery Connect.
- Managing screenshots, descriptions, categories, ratings, pricing, countries, privacy policies, or staged rollout.
- Supporting the legacy Huawei `client_id` plus `client_secret` authentication flow.
- Waiting until human review is approved or rejected as part of `publish`.
- Dynamic third-party adapter discovery in v0.1.0.
- Full Android manifest, certificate, or signature-chain analysis.
- A hosted API, database, browser UI, user system, or shared credential vault.

## 4. Architecture

The project uses a modular single-package architecture. The CLI is a thin input/output layer over an application service. Vendor behavior is isolated behind a `StoreAdapter` protocol. Infrastructure capabilities such as configuration, credentials, run storage, time, and HTTP transport are injected through focused interfaces so that business behavior can be tested without real external calls.

```text
CLI commands
    -> PublishingService / publishing state machine
        -> StoreAdapter protocol
            -> HuaweiAndroidAdapter
                -> HuaweiClient
                    -> Huawei Publishing API

ConfigLoader -----------\
CredentialProvider ------+-> PublishingService
RunRepository -----------/
ResultRenderer -----------> terminal or JSON consumer
```

The first release registers Huawei directly in an internal adapter registry. The `StoreAdapter` boundary remains stable so later stores can be added without changing the publishing orchestration. Dynamic Python entry-point plugins are deliberately deferred until the adapter contract has real multi-store usage.

### 4.1 Package layout

```text
src/storehelper/
  __init__.py                  public Python API
  cli.py                       Typer application and top-level error boundary
  commands/                    command argument parsing and presentation only
  config/                      strict YAML models and discovery
  credentials/                 environment, keyring, and prompt providers
  domain/                      results, errors, exit codes, and publishing types
  publishing/                  orchestration and state machine
  stores/base.py               StoreAdapter protocol
  stores/huawei/               Huawei auth, HTTP client, models, errors, package rules
  runs/                        atomic redacted run receipt repository
  output/                      text and JSON renderers
```

Dependencies flow inward. Huawei modules do not read terminal input or write run receipts. CLI commands do not perform Huawei HTTP requests directly. Renderers receive typed results and never inspect credentials.

## 5. Configuration

The default project configuration file is `storehelper.yaml` in the current working directory. `--config PATH` selects another file. v0.1.0 does not search parent directories, avoiding accidental use of a different project's release configuration.

```yaml
version: 1

apps:
  wallet:
    package_name: com.example.wallet
    stores:
      huawei:
        app_id: "123456789"
        credential_profile: company
        language: zh-CN
```

Requirements:

- `version` must equal integer `1`.
- `apps` must contain at least one named application.
- Application aliases use lowercase letters, digits, dashes, and underscores.
- `package_name`, Huawei `app_id`, and `credential_profile` are required non-empty strings.
- `language` defaults to `zh-CN`.
- Unknown fields are rejected.
- A single configured application is selected automatically when `--app` is omitted.
- Multiple configured applications require `--app`.
- Secrets are rejected if keys such as `private_key`, `access_token`, `client_secret`, or `password` appear anywhere in the configuration.
- `storehelper init` creates an example without overwriting an existing file.

## 6. Credential model

Only Huawei Service Account authentication is supported in v0.1.0:

```json
{
  "key_id": "...",
  "sub_account": "...",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...",
  "token_uri": "https://oauth-login.cloud.huawei.com/oauth2/v3/token"
}
```

`token_uri` is optional and defaults to Huawei's documented value. The importer normalizes a PEM value containing literal `\\n`, validates that the key is a loadable unencrypted private key, and stores the complete JSON value under service `storehelper:huawei` and username equal to the credential profile.

Credential resolution order is:

1. CI environment or secret file.
2. Operating-system keyring profile.
3. Secure terminal prompt when stdin is interactive.

The CI environment supports either all three values:

```text
STOREHELPER_HUAWEI_KEY_ID
STOREHELPER_HUAWEI_SUB_ACCOUNT
STOREHELPER_HUAWEI_PRIVATE_KEY
```

or a file path:

```text
STOREHELPER_HUAWEI_CREDENTIALS_FILE
```

Providing both forms is an error. A missing credential in a non-interactive process is an error. A missing or unusable system keyring never falls back to plaintext persistence.

Credential commands are:

```text
storehelper credentials import huawei --profile NAME --file PATH
storehelper credentials list
storehelper credentials verify huawei --profile NAME
storehelper credentials delete huawei --profile NAME
```

`list` returns profile names only. `verify` signs a JWT and calls a read-only Huawei endpoint. `delete` requires interactive confirmation unless `--yes` is supplied.

## 7. Command-line interface

```text
storehelper init
storehelper config validate
storehelper credentials import|list|verify|delete
storehelper publish
storehelper resume RUN_ID
storehelper status
storehelper runs list|show|delete
storehelper version
```

The complete publish command is:

```bash
storehelper publish \
  --app wallet \
  --store huawei \
  --file build/app-release.aab \
  --release-notes-file RELEASE_NOTES.md
```

Behavior flags:

- `--yes`: skip the destructive-action confirmation; required for non-interactive full submission.
- `--dry-run`: perform local configuration and package validation without authentication or network calls.
- `--no-submit`: upload, bind, and wait for compilation without modifying release notes or submitting review.
- `--output text|json`: select presentation; default `text`.
- `--poll-interval DURATION`: default `15s`, minimum `5s`.
- `--wait-timeout DURATION`: default `10m`, minimum equal to the polling interval.
- `--config PATH`: select configuration file.

Release notes come from exactly one of `--release-notes`, `--release-notes-file`, or an interactive prompt. They are required only when submission is requested. UTF-8 length must be between 1 and Huawei's documented 500-character limit. Overlong text is rejected rather than silently truncated. Only the selected language's `newFeatures` field is updated.

Full local publishing displays the app, app ID, package, SHA-256 prefix, and intended actions, then prompts `Continue? [y/N]`. JSON mode and non-interactive terminals never prompt; they require `--yes` for a full submission.

## 8. Package validation and naming

Local validation verifies:

- the path exists, is a regular readable non-empty file, and has a case-insensitive `.apk` or `.aab` suffix;
- the file size is no greater than 4 GiB;
- the ZIP central directory passes integrity validation;
- AAB contains `BundleConfig.pb` and a `base/manifest/AndroidManifest.xml` entry;
- APK contains `AndroidManifest.xml` and `META-INF` signing metadata when present, with missing legacy signature metadata reported as a warning rather than an error;
- SHA-256 can be streamed without loading the entire package into memory.

The logical upload file name removes known temporary storage prefixes, removes control characters and path separators, preserves the original `.apk` or `.aab` suffix, and is shortened to at most 64 Unicode code points. If no valid stem remains it uses `app-release`. The same logical name is used in multipart upload and package binding to avoid Huawei `204144662` file-name failures.

## 9. Huawei authentication and protocol

The JWT header is:

```json
{"alg":"PS256","kid":"<key_id>","typ":"JWT"}
```

The payload contains `aud`, `iss`, `iat`, and `exp`. `aud` equals the configured token URI, `iss` equals `sub_account`, and expiration is one hour. Signing uses RSASSA-PSS with SHA-256, MGF1 SHA-256, and salt length 32 bytes. The JWT is cached in memory only and renewed 60 seconds before expiry.

Android Publishing API v2 requests send:

```text
Authorization: Bearer <JWT>
client_id: <key_id>
```

The adapter performs:

1. Read-only credential and app verification.
2. `GET /upload-url` with `appId` and package suffix.
3. Multipart stream upload with the returned `authCode`.
4. `PUT /app-file-info` with `fileType=5`, the exact logical file name, and returned destination.
5. Capture a single `pkgVersion` from the binding response.
6. `GET /package/compile/status` using `appId` and `pkgVersion`.
7. `PUT /app-language-info` containing only `lang` and `newFeatures`.
8. `POST /app-submit` with `releaseType=1`.
9. `GET /app-info` for later review-state queries.

Temporary upload URLs, `authCode`, destination URLs, and JWTs are treated as secrets and are never returned outside the Huawei client.

## 10. Publishing state machine

```text
CREATED -> VALIDATED -> AUTHENTICATED -> APP_VERIFIED -> UPLOADING
        -> PACKAGE_BOUND -> PACKAGE_COMPILING -> PACKAGE_READY
        -> METADATA_UPDATED -> SUBMITTED -> COMPLETED
```

Alternate terminal or resumable states:

- `--dry-run`: `VALIDATED -> COMPLETED`.
- `--no-submit`: `PACKAGE_READY -> COMPLETED`.
- unrecoverable input, authentication, vendor, or compilation error: `FAILED`.
- compilation deadline: `TIMED_OUT`, resumable.
- SIGINT/CTRL-C: `INTERRUPTED`, resumable when `pkgVersion` is known.

Polling defaults to every 15 seconds for at most 10 minutes. `successStatus=0` means ready, `1` means processing, and `2` means failed. Legacy `aabCompileStatus` is accepted for compatibility. HTTP 429 honors `Retry-After`; HTTP 5xx is retried within the overall deadline; HTTP 401/403 causes one JWT renewal and retry. Huawei `204144727` after a ready status is treated as eventual consistency and returns to polling until the deadline.

`publish` completes when Huawei accepts the review submission. It does not wait for human review. `status` queries that separately.

## 11. Run receipts and idempotency

Run receipts are JSON schema version 1 files stored using platform-specific state directories:

- macOS: `~/Library/Application Support/StoreHelper/runs`
- Linux: `$XDG_STATE_HOME/storehelper/runs` or `~/.local/state/storehelper/runs`
- Windows: `%LOCALAPPDATA%\StoreHelper\runs`

Each write uses a temporary file, flush, file synchronization, and atomic replacement. Receipts contain only:

- run ID and schema version;
- store, app alias, app ID, package name;
- original path, logical file name, size, and SHA-256;
- Huawei `pkgVersion` and public submission ID;
- requested action, current state, safe vendor error code, message, and timestamps.

They never contain credentials, JWTs, authorization headers, `authCode`, upload URLs, destination URLs, or raw Huawei response bodies.

An unfinished receipt with the same store, app ID, and package SHA-256 is offered for resume rather than re-upload. JSON/non-interactive execution returns a resumable result and requires an explicit `resume` command. If interruption occurs after raw upload but before binding and no `pkgVersion` exists, the expired temporary operation is not reusable and a new upload is allowed.

`resume` starts at the earliest safe action for the saved state. Completed and failed runs are not resumed. `runs delete` affects only local redacted receipts and requires confirmation unless `--yes` is supplied.

## 12. Output contract

Text mode uses progress indicators and concise actionable messages. JSON mode emits exactly one JSON document to stdout. Progress, warnings, and diagnostics go to stderr. In JSON mode progress is disabled unless explicitly requested in a future release.

The result schema includes:

```json
{
  "schema_version": 1,
  "ok": false,
  "run_id": "20260730-153012-a1b2c3",
  "store": "huawei",
  "stage": "package_compiling",
  "resumable": true,
  "message": "Huawei is still compiling the package.",
  "vendor": {"code": "204144727"},
  "next_action": {
    "command": "storehelper resume 20260730-153012-a1b2c3 --output json"
  }
}
```

Raw vendor responses are not part of the public output contract.

## 13. Error taxonomy and exit codes

| Exit | Category | Examples |
| ---: | --- | --- |
| 0 | success | dry-run valid, no-submit ready, review submitted, status query succeeded |
| 2 | usage/configuration | invalid flags, malformed YAML, missing app, missing release notes |
| 3 | credentials/authentication | missing keyring profile, invalid PEM, Huawei 401/403 |
| 4 | package validation | missing, unreadable, invalid ZIP, unsupported extension, over 4 GiB |
| 5 | vendor rejection | bind failure, compile failure, metadata rejection, submit rejection |
| 6 | resumable timeout | package still compiling after deadline |
| 7 | local state failure | corrupt receipt, atomic write failure, unsupported receipt version |
| 8 | network failure | connection, DNS, TLS, or repeated transient HTTP failures |
| 130 | interrupted | SIGINT/CTRL-C |

Every domain exception carries a stable code such as `CONFIG_INVALID`, `CREDENTIAL_NOT_FOUND`, `PACKAGE_INVALID`, `HUAWEI_PACKAGE_COMPILING`, or `NETWORK_UNAVAILABLE`. User messages may improve without changing those codes.

Known Huawei codes receive actionable translations, including `204144662`, `204144727`, authentication failures, signature mismatch, package detection failure, and missing pre-submit information. Unknown codes preserve only the numeric code and a size-limited sanitized message.

## 14. Security requirements

- No secret CLI flags such as `--private-key` or `--client-secret`.
- No plaintext credential fallback.
- No secret values in exception strings, command echoes, logs, progress output, JSON, or receipts.
- Redaction covers JWT-shaped values, PEM blocks, authorization headers, Huawei upload authorization values, and credential field names.
- Configuration parsing rejects embedded secrets.
- Credential files are read only when explicitly provided and are never deleted automatically.
- CI secret files are not copied and are not included in artifacts.
- HTTP redirects are disabled for authenticated Huawei API requests; upload redirects are accepted only when the target remains HTTPS and no Huawei authorization header is forwarded.
- TLS verification stays enabled and has no CLI disable switch.
- Local state uses owner-only permissions where the platform supports them.
- Log level defaults to informational progress without HTTP bodies; debug mode remains sanitized.

## 15. Testing strategy

Development follows test-driven development: write a behavioral test, observe the expected failure, implement the minimum behavior, and rerun the focused and full suites.

Unit tests cover:

- strict configuration, application selection, and secret-key rejection;
- duration parsing and CLI option compatibility;
- PEM normalization, JWT claims, PS256 salt length, caching, and renewal;
- environment, keyring, and prompt credential precedence;
- filename normalization, ZIP validation, SHA-256 streaming, and boundary sizes;
- Huawei response parsing, `pkgVersion`, compile status, error mapping, and redaction;
- state transitions, resume decisions, duplicate detection, atomic receipt writes, and schema rejection;
- result rendering, stdout/stderr separation, and exit-code mapping.

HTTP contract tests use `respx` to replace only the external Huawei boundary. Fixtures mirror complete documented or observed response shapes. They verify method, URL, query, headers, multipart name, safe binding payload, retry behavior, and that raw temporary values never escape the client.

CLI tests use Typer's `CliRunner`, real temporary configuration and receipt repositories, and injected fake adapters. They cover full publish, dry-run, no-submit, confirmation rejection, non-interactive requirements, timeout, interruption, resume, status, and valid JSON stdout.

No test calls the live Huawei API. A manual live smoke-test checklist is documented but requires the maintainer's credentials and explicit execution.

Quality gates are:

```text
ruff format --check .
ruff check .
mypy src
pytest --cov=storehelper --cov-report=term-missing --cov-fail-under=90
python -m build
pipx run --spec dist/<wheel> storehelper version
```

CI runs supported Python versions 3.11 through 3.14. Network-free tests must pass on Linux, macOS, and Windows before v0.1.0.

## 16. Distribution

The canonical Python distribution name and PyPI availability are verified before the first public upload. The console command remains `storehelper` even if the distribution name needs a namespace suffix.

Release sequence:

1. Merge verified code through a pull request.
2. Tag a semantic version such as `v0.1.0`.
3. Use GitHub Actions OIDC Trusted Publishing to upload wheel and source distribution to PyPI.
4. Create a GitHub Release with generated release notes, packages, checksums, and supported-platform notes.
5. After the first stable tagged artifact, create `StoreHelper/homebrew-tap` and point its formula at immutable release artifacts.

Primary installation is:

```bash
pipx install storehelper
```

GitHub Release native executables may be added after the wheel workflow is stable. Homebrew is an additional macOS/Linux convenience channel, not the sole installation path.

## 17. Acceptance criteria

The milestone is complete when all of the following are true:

- A new user can install the package in an isolated Python 3.11+ environment and run `storehelper --help`.
- `storehelper init` generates a valid secret-free multi-app configuration.
- A Service Account JSON can be imported into a supported keyring and verified without displaying secret material.
- Dry-run validates a representative APK and AAB without network access.
- Contract tests prove upload URL acquisition, streamed upload, package binding, `pkgVersion` capture, compile polling, release-note update, review submission, and status query.
- `204144727`, HTTP 429/5xx, timeout, and interruption produce correct resumable behavior without duplicate binding.
- A saved run can resume after process restart using only redacted receipt data and freshly resolved credentials.
- `--output json` writes one schema-valid document to stdout and no secret fixture appears in any captured output or receipt.
- Formatting, linting, type checking, at least 90% test coverage, package build, and installed CLI smoke test pass.
- README, configuration example, security guidance, and release checklist accurately describe the implemented behavior.

