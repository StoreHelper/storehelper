# OPPO Software Store v0.6.0 Design

## Goal

Add a production-oriented OPPO Software Store adapter to the Python 3.11+ StoreHelper CLI and
SDK. It publishes one signed APK update to an existing mainland-China OPPO package, submits the
new version for review, and exposes review status without persisting credentials, tokens,
signatures, temporary upload URLs, listing snapshots, or raw vendor responses.

Automated tests must never call OPPO. A real developer account is used only through the explicit
manual checklist after a release owner separately chooses to run it.

## Evidence and confidence boundary

OPPO's public documentation catalog currently exposes **接口鉴权** and **应用更新服务**, but the
current protocol detail endpoint requires an authenticated developer session. The in-repository
Java and MCP adapters are therefore the executable reference for the request field names and
endpoints below; the official OPPO site remains the authority for account enablement and live
acceptance.

The implemented contract is deliberately isolated behind one adapter and fully mocked in tests.
The manual checklist starts with read-only authentication/application verification. If the live
contract differs, StoreHelper must fail closed with a sanitized error; it must not guess alternate
hosts, endpoints, fields, or signing rules.

Official entry points:

- <https://open.oppomobile.com/new/wiki>
- <https://open.oppomobile.com/documentation/page/center>

## Supported scope

v0.6.0 supports only:

- an existing OPPO Software Store application owned by the credential's application client;
- a single signed `.apk` no larger than 2 GiB;
- a positive configured Android `version_code` that is greater than the currently published value;
- a full online update (`online_type=1`);
- one 1–500 character update description supplied as StoreHelper release notes;
- reuse of the existing OPPO name, categories, summary, long description, privacy URL, icon, and
  screenshots returned by application detail;
- asynchronous review-status mapping through application detail.

It does not create or claim an app, publish a game, upload AAB/multiple APKs, change listing
metadata, configure phased rollout, schedule release, replace screenshots/icons, or create a
draft. `--no-submit` is unsupported because the temporary upload object is intentionally not
persisted and has no useful safe handoff to a later CLI process.

The configured `version_code` must match the signed APK. StoreHelper cannot portably extract
binary Android manifest metadata without Android build tools, so OPPO remains the authoritative
validator for that equality.

## User-facing configuration

Only public, release-specific values belong in `storehelper.yaml`:

```yaml
version: 1
apps:
  example-app:
    package_name: com.example.app
    stores:
      oppo:
        credential_profile: oppo-example-app
        version_code: 123
        language: zh-CN
```

`version_code` is a positive integer. The profile is application-specific because OPPO issues a
client pair per application. Unknown fields remain rejected.

The resolved neutral `StoreTarget` uses the package name as `app_id`, carries `version_code` as an
optional positive integer, and never includes client credentials.

## Credentials

The OPPO credential is exactly:

```json
{
  "client_id": "replace-with-oppo-client-id",
  "client_secret": "replace-with-oppo-client-secret"
}
```

Both values are non-empty and bounded. The credential has its own `CredentialKind.OPPO_API` and
keyring namespace; it never aliases Xiaomi, Huawei, Apple, or Google credentials. Resolution
precedence is:

1. `STOREHELPER_OPPO_CREDENTIALS_FILE`;
2. the complete pair `STOREHELPER_OPPO_CLIENT_ID` and `STOREHELPER_OPPO_CLIENT_SECRET`;
3. the `oppo` keyring profile;
4. secure interactive prompts.

File and individual environment forms are mutually exclusive. Secret values are excluded from
repr, validation errors, text/JSON output, receipts, logs, and exceptions.

## Protocol and authentication

The only API origin is `https://oop-openapi-cn.heytapmobi.com`; redirects are rejected.

1. `GET /developer/v1/token` uses the application `client_id` and `client_secret` and returns a
   short-lived access token. The token and token request URL are never logged or persisted.
2. Business parameters add `access_token` and an integer Unix `timestamp`.
3. Parameters other than `api_sign` and empty values are sorted by ASCII key and joined exactly as
   `k1=v1&k2=v2` before HMAC-SHA256 with `client_secret`; `api_sign` is lowercase hexadecimal.
4. `GET /resource/v1/app/info` verifies exact package ownership, current `version_code`, current
   review state, and the required existing listing fields.
5. `GET /resource/v1/upload/get-upload-url` returns the upload endpoint and upload sign.
6. One streamed multipart upload sends `type=apk`, the returned `sign`, and field `file`. The
   returned file URL remains only inside the adapter instance.
7. `POST /resource/v1/app/upd` sends a signed form. `apk_url` is compact JSON containing exactly
   one `{url, md5}` object. Existing public listing values are reused, `update_desc` comes from
   release notes, `test_desc` is the note bounded to 400 characters, and `online_type=1`.

MD5 is used only where the vendor protocol requires APK identity. StoreHelper receipts and local
duplicate detection continue using SHA-256.

Token and application-detail reads receive bounded transient retries. Upload-address allocation,
APK upload, and final submission receive zero automatic mutation retries. Error bodies are parsed
through an allowlist and sanitized before they cross the client boundary.

## Upload-host safety

The dynamic upload URL must be HTTPS, contain no userinfo or fragment, use the default TLS port,
and belong to an explicit OPPO/HeyTap suffix allowlist maintained beside the client. Literal,
loopback, link-local, private, multicast, and reserved IP hosts are rejected. Redirects are never
followed. The returned APK file URL is accepted only as an opaque value for the immediately
following signed submission; it is never fetched, printed, logged, or written to disk.

## Staged non-resumable submission architecture

OPPO is neither a normal resumable store nor Xiaomi's one-request atomic store. StoreHelper adds a
`staged_submission` capability and `StagedStoreAdapter` protocol:

```python
class StagedStoreAdapter(StoreAdapter, Protocol):
    async def stage_submission(
        self,
        *,
        target: StoreTarget,
        artifact: ArtifactInfo,
        release_notes: str | None,
    ) -> None: ...

    async def commit_staged_submission(self, *, target: StoreTarget) -> str: ...
```

The first method performs the read-only listing query, allocates an upload URL, streams the APK,
and retains the URL/listing snapshot only in memory. The publisher then writes the public
`metadata_updated` state, immediately writes `submission_started`, and calls the final method.

- Failure or cancellation before `submission_started` becomes `failed`; a new confirmed run may
  safely upload again because no review submission was attempted.
- Cancellation, process loss, timeout, or network loss after `submission_started` becomes or
  remains `submission_uncertain`; the same package/artifact is blocked from automatic retry.
- `resume` is unsupported. No receipt contains enough temporary state to resume safely.
- Operators use `status` and the OPPO console to reconcile an uncertain final request, then
  explicitly delete the local receipt before a separately approved retry.

Direct Xiaomi behavior remains unchanged. Existing Huawei, HarmonyOS, Apple, and Google resumable
flows remain unchanged.

## Application verification and metadata rules

`verify` obtains a token and exact package detail. It rejects a missing/mismatched package,
credential/auth failure, frozen or unsupported application state, a configured version that is not
greater than the current numeric version, and absent required listing fields.

Before upload, normal publish repeats the detail query through `stage_submission` so the submitted
snapshot is fresh. Values are bounded to the vendor constraints in the adapter. StoreHelper does
not silently replace missing required fields with empty strings or generic copy; the operator must
fix the listing in the OPPO console.

## Review status

`status --store oppo` performs token acquisition plus signed application detail. It maps:

- `0` to `pending_review`;
- `1` and `4` to `in_review`;
- `2`, `6`, `7`, and `111` to `approved`;
- `3`, `5`, and `444` to `rejected`;
- `222` to `suspended`;
- unrecognized values to `unknown`.

Refusal text and raw application details are not returned by the stable v1 `OperationResult`; the
sanitized state remains the public contract.

## Errors and output safety

Errors use stable StoreHelper codes, including authentication/signature failure, package missing,
version conflict, incomplete listing, unsafe upload host, upload rejection, review conflict, and
uncertain final submission. Known numeric `errno` values map to safe operator hints. Unknown
responses expose only `errno`; raw bodies, URLs, tokens, signatures, client values, and listing
payloads never appear in public messages.

The client never logs query strings, request bodies, headers, multipart data, or successful upload
URLs. Receipt schema v5 remains sufficient because no new persisted field is needed.

## Testing and release gates

Every implementation task follows red-green-refactor. Tests use fake credentials, local generated
APK ZIPs, `respx`, and deterministic timestamps. They cover canonical signing, credential source
conflicts, fixed origins, redirects, transient read bounds, zero mutation retries, exact form and
compact JSON, streamed upload, upload-host rejection, status mapping, ambiguous final results,
duplicate blocking, no resume/no-submit, and secret-safe output/receipts.

The milestone closes only after:

- Ruff format/check and strict mypy pass;
- all existing and OPPO tests pass with at least 90% package coverage;
- v0.6.0 sdist/wheel pass Twine checks;
- the exact wheel installs in a fresh environment;
- installed help/version, six-store config validation, and six offline dry-runs pass;
- archive and non-test secret scans pass;
- no automated command contacts OPPO.

## Documentation

README, security guide, example YAML, development plan, and a bilingual
`docs/OPPO_MANUAL_TEST.md` describe API-client creation, application-specific credential storage,
existing-app/version constraints, dry-run, read-only credential verification, separately approved
submission, status reconciliation, uncertain-state handling, and local receipt deletion.
