# HONOR App Market v0.8.0 Design

## Goal

Add a conservative, local-first HONOR adapter that publishes one APK update to an existing
mainland-China application, updates only the selected locale's version notes, submits one
immediate full release for review, supports safe recovery, and exposes read-only review status.

## Official protocol evidence

The implementation is bounded by HONOR's current official documentation:

- [Publish API guide](https://developer.honor.com/cn/doc/guides/101359)
- [APK release guide](https://developer.honor.com/cn/doc/guides/100884)
- [App Market review guideline](https://developer.honor.com/cn/doc/guides/100879)

The Publish API guide documents the complete flow used here: client-credentials token, package to
APPID lookup, application detail/current-release queries, upload allocation, fixed-host multipart
file upload, file binding, localized information update, audit submission, and audit status query.
No local production implementation was found in the available workspace, so exact tests follow
the official request/response fields rather than an inferred vendor SDK. No automated verification
contacts HONOR.

## Supported scope

- Existing mainland-China phone application only; the parent YAML package is the HONOR identity.
- One readable, structurally valid APK strictly smaller than 4 GiB.
- A positive configured `version_code` greater than the currently published version code.
- Release notes contain 1–500 characters and update only `newFeature` for the configured locale.
- Non-forced, immediate full publication after approval (`forceUpdate=0`, `releaseType=1`).
- Other app information, locales, media, privacy, classification, regions, and release policy are
  preserved.

StoreHelper does not create/claim an application, publish games for the first time, upload HAP/AAB
or multiple APKs, change listing metadata, schedule or phase publication, manage test accounts, or
use the public-URL ingestion alternative in v0.8.0.

## Public configuration and credentials

Public project YAML contains only:

```yaml
honor:
  credential_profile: honor-release
  version_code: 125
  language: zh-CN
```

The independent `storehelper:honor` keyring namespace stores exactly:

```json
{
  "client_id": "...",
  "client_secret": "..."
}
```

Both values use `SecretStr` and are treated as sensitive. Sources, in fixed precedence order, are
one credential file, the complete `STOREHELPER_HONOR_CLIENT_ID` plus
`STOREHELPER_HONOR_CLIENT_SECRET` pair, OS keyring, and secure interactive prompts. Mixed or partial
environment sources fail closed.

## Network and authentication boundary

- Token endpoint: `https://iam.developer.honor.com/auth/token` with
  `grant_type=client_credentials`.
- Publish API base: `https://appmarket-openapi-drcn.cloud.honor.com`.
- Every path is selected from a static allowlist and every redirect is rejected.
- Tokens are cached only in memory until shortly before `expires_in`, then refreshed. A 401 may
  force one refresh.
- Read-only GETs may retry connection failures, HTTP 429, and HTTP 5xx twice with bounded backoff.
- Upload allocation, multipart upload, file binding, locale update, and audit submission are never
  automatically replayed in the same call.
- JSON bodies are size-bounded. Authorization headers, credentials, tokens, full app snapshots,
  upload responses, and vendor-controlled error text are never logged or persisted.

HONOR documents a fixed `file-upload` endpoint using `appId` and `objectId`; StoreHelper uses that
endpoint and ignores the returned `uploadUrl`. It does not require a public artifact URL and never
forwards the bearer token outside the fixed HONOR API host.

## Read, upload, and preparation flow

1. Exchange the client pair for an account-level token.
2. Resolve exactly one APPID for the exact package via `get-app-id`.
3. Read `get-app-detail` and `get-app-current-release`; require exact package ownership, an existing
   published version, the configured locale, a higher configured version, and no conflicting
   review/draft state.
4. Allocate one APK object with `fileType=100`, logical filename, byte length, and lowercase SHA-256.
5. Stream one multipart field named `file` to the fixed `file-upload` endpoint with zero retries.
6. Persist only `operation_id=APPID` and `artifact_id=objectId:sha256` for recovery.
7. Re-query detail. Bind only the new object when no APK file already has the expected SHA-256.
8. Re-query the configured locale. Preserve its exact `appName`, `intro`, and optional
   `briefIntro`; change only `newFeature`; send `setAll=0` so other locales are untouched.
9. Submit exactly `{forceUpdate: 0, releaseType: 1}` after reconciling current release state.

## Recovery and uncertainty

`--no-submit` is rejected because an allocated but unbound object is temporary and not a useful
durable draft. Normal interrupted publishing remains resumable:

- loss before upload completion leaves no receipt object and a new allocation/upload is safe;
- file binding is reconciled by the expected APK SHA-256 before another bind;
- locale update is reconciled by the expected `newFeature` before another update;
- audit submission is reconciled by configured version code, non-editing audit state, and release
  ID before another submit;
- after an ambiguous submit response, the client performs read-only reconciliation only and never
  sends a second submission automatically.

Persisted APPID, object ID, SHA-256, and release ID are public durable operation identifiers. Tokens,
credentials, app `secretKey`, test accounts/passwords, upload URLs, full listing data, and audit
attachments are excluded from receipts.

## Status and conflict policy

`get-app-current-release.auditResult` maps as follows:

| HONOR value | StoreHelper status |
| --- | --- |
| `0` | `in_review` |
| `1` | `approved` |
| `2` | `rejected` |
| `3` | `unknown` |
| `4` | `pending_review` |
| missing/anything else | `unknown` |

Before a new upload, `0`, `3`, and `4` fail closed. `1` is allowed only when it represents the
currently published lower version. `2` permits correcting a rejected update when the configured
version remains greater than the published version. Unknown or inconsistent states never silently
become publishable.

## Verification

Tests use `httpx.MockTransport` only. They cover credentials/token caching, fixed paths, strict JSON
validation, exact package/APPID/locale/version checks, streamed upload, reconciliation of every
mutation, review mapping, redirects/retries, safe errors, receipts, CLI commands, and redaction.
Release closure requires Ruff, strict mypy, all tests with at least 90% coverage, archive inspection,
Twine validation, exact-wheel installation, and offline dry-runs for all eight supported stores.
