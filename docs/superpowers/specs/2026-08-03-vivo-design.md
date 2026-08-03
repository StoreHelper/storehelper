# vivo App Store v0.7.0 Design

## Goal

Add a conservative, local-first vivo adapter that publishes one APK update to an existing
mainland-China vivo application, submits it for review exactly once, and exposes read-only review
status through the existing CLI and Python API.

## Evidence and protocol boundary

The public documentation entry points are the vivo Open Platform pages for the application API
and application update API:

- <https://dev.vivo.com.cn/documentCenter/doc/326>
- <https://dev.vivo.com.cn/documentCenter/doc/327>

Those pages currently render their body through client-side JavaScript and cannot be read by the
automated documentation reader. Exact request fields are therefore cross-checked against the
repository's already-running Java and Python vivo implementations. StoreHelper will deliberately
support only their common, proven subset:

- fixed gateway: `https://developer-api.vivo.com.cn/router/rest`;
- methods: `app.query.details`, `app.upload.apk.app.64`, and `app.sync.update.app`;
- HMAC-SHA256 authentication with `access_key` and `secret_key`;
- one APK upload followed by one version-update submission;
- read-only application/review status query.

No live vivo request is part of automated implementation or verification.

## Supported scope

- Existing application only; the YAML application package is the vivo package identity.
- One readable, structurally valid APK no larger than 3 GiB.
- A positive configured `version_code` greater than the current vivo version code.
- Release notes contain 5–200 characters after trimming.
- Immediate publication after approval (`onlineType=1`) for phone (`compatibleDevice=1`).
- Existing listing, icon, screenshots, classification, privacy materials, and descriptions remain
  unchanged.

StoreHelper does not create an application, upload AAB/multiple APKs, change listing metadata,
schedule publication, configure phased rollout, or upload privacy self-check documents in v0.7.0.

## Public configuration and credentials

Public project YAML adds only:

```yaml
vivo:
  credential_profile: vivo-release
  version_code: 123
  language: zh-CN
```

The package name continues to come from the parent application. The locale is retained for a
stable cross-store configuration model but is not sent by this protocol subset.

The independent `storehelper:vivo` keyring namespace stores exactly:

```json
{
  "access_key": "...",
  "secret_key": "..."
}
```

Both values are sensitive and use `SecretStr`. Supported sources, in precedence order, are one
credentials file, the complete `STOREHELPER_VIVO_ACCESS_KEY` plus
`STOREHELPER_VIVO_SECRET_KEY` pair, the OS keyring, and a secure interactive prompt. Mixed or
partial environment sources fail closed.

## Authentication

Every request uses these common parameters:

- `method`
- `access_key`
- millisecond `timestamp`
- `format=json`
- `v=1.0`
- `sign_method=HMAC-SHA256`
- `target_app_key=developer`

Common and business parameters are merged, `sign` is excluded, keys are sorted in ASCII order,
and literal `key=value` pairs are joined with `&`. The lowercase hexadecimal HMAC-SHA256 digest
uses `secret_key` as the UTF-8 key. Signing inputs, the secret, the resulting signature, and full
forms are never rendered, logged, or persisted.

## Read and mutation flow

`verify` sends a signed `app.query.details` form for the exact package. It requires a matching
package identity, a positive current version code, a known numeric status, and a configured version
code greater than the current value. Statuses `1` (pending/draft), `2` (in review), and `6`
(suspended/offline) block automatic submission. Statuses `3`, `4`, and `5` permit a new version;
`4` deliberately permits correcting a rejected update.

Publishing uses the existing staged-submission boundary:

1. Re-query and validate the application.
2. Stream the APK once to `app.upload.apk.app.64` as multipart field `file`, signed together with
   `packageName` and the locally streamed lowercase MD5.
3. Keep the returned `serialnumber` and `fileMd5` only in adapter memory.
4. Persist `submission_started` immediately before the final mutation.
5. Send exactly one `app.sync.update.app` form with only `packageName`, `versionCode`, `apk`,
   `fileMd5`, `onlineType=1`, `compatibleDevice=1`, and `updateDesc`.

The final request does not send application name, description, classification, icon, screenshots,
or privacy fields. This avoids accidental listing changes and the known conflict caused by calling
the update method twice.

## Retry and uncertainty policy

- Read-only application queries may retry connection failures, HTTP 429, and HTTP 5xx twice with
  bounded backoff.
- Upload and final submission have zero automatic retries.
- Redirects are always rejected and the client never changes origin or path.
- Upload failure occurs before `submission_started`, so a newly confirmed run is safe.
- Cancellation, transport loss, or process loss after `submission_started` becomes
  `submission_uncertain` and blocks an automatic duplicate.
- `--no-submit` and `resume` are unsupported because the upload serial number is temporary and
  intentionally not persisted.
- Operators reconcile uncertain submissions with `status --store vivo` and the vivo console,
  then deliberately delete the local receipt before authorizing a new run.

## Response and status handling

Responses must be bounded JSON objects. Success requires top-level `code=0` and absent or zero
`subCode`. Errors expose only stable StoreHelper error codes plus numeric/string vendor codes; raw
responses, request values, vendor forms, and secret-bearing context are never included.

The documented/reference status mapping is:

| vivo status | StoreHelper status |
| --- | --- |
| `1` | `pending_review` |
| `2` | `in_review` |
| `3` | `approved` |
| `4` | `rejected` |
| `5` | `approved` |
| `6` | `suspended` |
| anything else | `unknown` |

Unknown values never silently become pending or approved.

## Verification

Tests use `httpx.MockTransport` only. They cover deterministic signing, strict credentials and
configuration, APK/MD5 validation, exact fixed-host requests, redirects, bounded read retries,
zero mutation retries, safe response parsing, staged uncertainty behavior, status mapping,
CLI output/receipts, and redaction. Release closure requires Ruff, strict mypy, all tests with at
least 90% coverage, archive inspection, Twine validation, exact-wheel installation, and offline
dry-runs for all seven supported stores.
