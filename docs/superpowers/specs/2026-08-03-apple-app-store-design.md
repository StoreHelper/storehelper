# Apple App Store Connect Publishing Design

**Status:** Approved under the maintainer's standing authorization to use the recommended design
**Date:** 2026-08-03
**Target release:** v0.3.0

## 1. Context

StoreHelper v0.2.0 has a store-neutral publishing state machine and production-capable Huawei
Android and HarmonyOS adapters. The next increment adds an Apple adapter for an existing iOS app
and an existing editable App Store version.

The preferred upload mechanism changed recently. App Store Connect API 4.1 added first-party
`buildUploads` and `buildUploadFiles` resources, including resumable file reservations and
`BUILD_UPLOAD_STATE_UPDATED` webhook events. StoreHelper will use that native API instead of
shelling out to Xcode, `altool`, or Transporter. This keeps the CLI cross-platform, testable with
mocked HTTP, and independent of a globally installed Apple toolchain.

Authoritative references used for this design:

- [App Store Connect API 4.1 release notes](https://developer.apple.com/documentation/appstoreconnectapi/app-store-connect-api-4-1-release-notes)
- [Build uploads](https://developer.apple.com/documentation/appstoreconnectapi/build-uploads)
- [Generating tokens for API requests](https://developer.apple.com/documentation/appstoreconnectapi/generating-tokens-for-api-requests)
- [Review submissions](https://developer.apple.com/documentation/appstoreconnectapi/review-submissions)
- [Platform version information](https://developer.apple.com/help/app-store-connect/reference/app-information/platform-version-information)
- Apple's current App Store Connect OpenAPI specification, downloaded from the official
  `app-store-connect-openapi-specification.zip` endpoint on 2026-08-03.

## 2. Goals

- Add `--store apple` for an existing iOS App Store Connect app.
- Validate and inspect one `.ipa` locally without Xcode or executing bundle content.
- Support team and individual App Store Connect API keys.
- Upload the IPA through native `buildUploads` and `buildUploadFiles` operations.
- Poll upload/import processing and capture the resulting public Build resource ID.
- Attach the processed build to one pre-existing App Store version.
- Update only one localization's `whatsNew` value.
- Submit that version through the nondeprecated review-submission workflow.
- Query a normalized review state.
- Preserve recovery after file commit, and reconcile reusable server-side reservations after a
  transfer interruption without creating a second upload job.
- Keep all tests network-free and make live verification explicitly opt-in.

## 3. Non-goals

- Creating an app record, bundle ID, App Store version, certificates, or provisioning profiles.
- Building, signing, notarizing, or exporting an IPA.
- macOS `.pkg`, tvOS, visionOS, watch-only, TestFlight distribution, phased release, pricing,
  availability, screenshots, review credentials, export-compliance declarations, or rollback.
- Running a webhook receiver in a local CLI. Apple exposes `BUILD_UPLOAD_STATE_UPDATED`, but
  persistent webhook handling belongs in the future MCP/service package.
- Falling back silently to Transporter when the native API rejects a request.

## 4. Approaches considered

### A. Native Build Upload API — selected

Use JSON:API endpoints for upload creation, file reservation, signed range uploads, commit,
processing, build attachment, metadata, and review submission. It works on macOS, Linux, and
Windows and can be verified without external binaries.

### B. Transporter subprocess

Transporter is mature but requires a separately installed macOS/Java toolchain, emits
tool-specific logs, and makes deterministic recovery and secret redaction harder. It remains a
documented manual fallback outside StoreHelper.

### C. Native API with automatic Transporter fallback

This broadens platform and failure semantics without improving the common case. Automatic
fallback could also upload the same binary twice. It is intentionally excluded.

## 5. Configuration

Configuration schema version remains `1`; existing files remain valid.

```yaml
version: 1

apps:
  wallet:
    package_name: com.example.wallet
    stores:
      apple:
        app_id: "1234567890"
        bundle_id: com.example.wallet.ios
        app_store_version_id: 11111111-2222-3333-4444-555555555555
        credential_profile: apple-release
        platform: IOS
        language: zh-Hans
```

`app_id` is the opaque App Store Connect `apps` resource ID, not a secret. `bundle_id` must match
the primary app inside the IPA. `app_store_version_id` identifies the already-created editable
`appStoreVersions` resource. v0.3.0 accepts only platform `IOS`.

The selected App Store version must match the IPA's `CFBundleShortVersionString`. Verification
also confirms that both the app and version relationships belong to the configured target before
any upload reservation is created.

## 6. Credentials

Apple credentials are a separate `CredentialKind.APPLE_API_KEY` profile with:

- `key_type`: `team` or `individual`;
- `key_id`;
- `issuer_id`: required for team keys and forbidden for individual keys;
- an unencrypted P-256 PKCS#8 private key from the downloaded `.p8` file.

The import command becomes store-aware:

```bash
storehelper credentials import --store apple --profile apple-release --file AuthKey.json
storehelper credentials verify --store apple --app wallet
```

Keyring values remain type-tagged JSON. Huawei profiles stay readable without migration. CI may
use either a single `STOREHELPER_APPLE_CREDENTIALS_FILE` or the complete individual variables:

- `STOREHELPER_APPLE_KEY_TYPE`;
- `STOREHELPER_APPLE_KEY_ID`;
- `STOREHELPER_APPLE_ISSUER_ID` for team keys only;
- `STOREHELPER_APPLE_PRIVATE_KEY`.

Team JWT payloads contain `iss`, `iat`, `exp`, and `aud=appstoreconnect-v1`. Individual JWTs use
`sub=user` instead of `iss`. Headers use `alg=ES256`, `kid`, and `typ=JWT`. Tokens last ten
minutes, refresh once after 401, and are never written to disk or output.

## 7. IPA validation

`validate_ipa(path)` performs local read-only checks:

1. Require a nonempty readable `.ipa` ZIP file whose size fits Apple's OpenAPI `int64` range.
2. Reject corrupt ZIPs and absolute, parent-traversing, or drive-qualified members.
3. Require exactly one top-level `Payload/<name>.app/Info.plist`.
4. Parse XML or binary plist with Python `plistlib`, without extraction.
5. Require nonempty `CFBundleIdentifier`, `CFBundleShortVersionString`, and `CFBundleVersion`.
6. Compute SHA-256 by streaming and produce a bounded ASCII logical `.ipa` filename.

The returned `AppleArtifactInfo` wraps the generic artifact fields plus bundle ID, marketing
version, build version, and platform. Validation rejects a bundle-ID mismatch before credentials
or network access.

## 8. Native build upload protocol

The adapter follows the current OpenAPI schema exactly:

1. Search the app's `AWAITING_UPLOAD` build uploads for the exact platform, marketing version,
   and build version. Reuse exactly one matching upload/file reservation; reject ambiguity.
   Otherwise `POST /v1/buildUploads` with those values and an `apps` relationship.
2. Reuse the matching reserved asset file, or `POST /v1/buildUploadFiles` with
   `assetType=ASSET`, `uti=com.apple.ipa`, file name/size, and the new `buildUploads`
   relationship.
3. Validate every returned `DeliveryFileUploadOperation`:
   - HTTPS URL with no user information;
   - method `PUT`;
   - positive length, nonnegative offset, unique positive part number;
   - exact, gap-free, nonoverlapping coverage of the IPA;
   - nonempty request-header names and values;
   - expiration that has not passed.
4. Treat an operation with a nonempty `entityTag` as already delivered; upload the remaining
   operations sequentially in offset order. Seek to each offset and stream exactly its declared
   length in 1 MiB chunks. Send only Apple's operation headers; never send the App Store Connect
   bearer token to the delivery host. Disable redirects.
5. `PATCH /v1/buildUploadFiles/{id}` with `uploaded=true` and
   `sourceFileChecksums.file={algorithm: SHA_256, hash: <hex>}`.

After all ranges are committed, the receipt persists the public build-upload ID as the processing
handle. An interruption before commit returns a terminal local run; rerunning `publish` performs
the exact-match reconciliation above and reuses the reservation and any operations that Apple
marks delivered. Upload URLs, headers, asset tokens, operation descriptors, response bodies, and
entity tags are ephemeral secrets and must never enter logs, exceptions, JSON results, or
receipts.

## 9. Processing and build selection

Poll `GET /v1/buildUploads/{id}?include=build`:

- `AWAITING_UPLOAD` and `PROCESSING` map to generic `PROCESSING`;
- `FAILED` maps to `FAILED` with only sanitized state-detail code/title text;
- `COMPLETE` is ready only when the related Build ID is present.

`ProcessingStatus` gains an optional replacement `artifact_id`. On completion, the state machine
atomically replaces the build-upload ID with the final Build ID before entering `PACKAGE_READY`.
This keeps one generic receipt field while making its meaning state-dependent and explicit:

- before `PACKAGE_READY`: upload/processing handle;
- at and after `PACKAGE_READY`: final vendor artifact/build ID.

Timeout and interruption before completion resume by polling the stored build-upload ID. Recovery
after completion uses the stored Build ID and never repeats upload operations.

## 10. Release preparation and review submission

The generic adapter contract changes to pass the complete `StoreTarget` to every operation.
Release preparation becomes
`prepare_release(target, artifact_id, release_notes: str | None)`, which is always called for a
full submission; submission becomes `submit(target, artifact_id)`. Huawei adapters use release
preparation to update required notes and ignore target fields they don't need. Apple uses it to
attach the build and optionally update `whatsNew`.

For Apple full submission:

1. `PATCH /v1/appStoreVersions/{versionId}/relationships/build` with the processed Build ID.
2. If release notes were supplied, find exactly one localization for the configured locale and
   patch only `whatsNew` (1–4000 characters). If notes were omitted, preserve existing metadata;
   this supports first versions, for which Apple doesn't expose `whatsNew`.
3. Reconcile a reusable `READY_FOR_REVIEW` review submission for the configured app. Create one
   only when no reusable draft exists.
4. Ensure one review-submission item relates the configured App Store version. Reuse an existing
   item on resume and reject an item that points to another version.
5. `PATCH /v1/reviewSubmissions/{id}` with `submitted=true`.

The deprecated `appStoreVersionSubmissions` API is not used. A 409/422 response is parsed as a
safe actionable vendor rejection; StoreHelper never changes screenshots, review credentials,
privacy data, pricing, release type, or other metadata to make submission pass.

## 11. Store-neutral contract and receipt changes

`StoreName` adds `APPLE`; `StoreTarget` adds optional `release_id` and `platform`. Adapter methods
receive `StoreTarget` instead of individual app-ID/package-name arguments. `processing_status`
may return a final artifact ID, and release preparation receives that ID. Review status also
receives the complete target so Apple can query the configured App Store version.

Receipt schema advances to version `3` and adds optional `release_id` and `submission_id`.
Version 1 and 2 receipts continue to migrate on read. The Apple adapter reconciles server state
before creating review resources, so interruption inside a multi-request submission is safe even
before the submission ID reaches the local receipt.

Duplicate detection remains `(store, app_id, artifact_sha256)`. Resume verifies store, app ID,
bundle ID, release ID, and local artifact digest against current configuration.

## 12. Error handling and retry policy

- Parse JSON:API `errors[]` into a bounded message using only status, code, title, and sanitized
  detail. Never echo request bodies, tokens, upload operations, URLs, or response headers.
- Refresh JWT once on 401. Treat 403 as authentication/authorization failure.
- Retry 429 and 5xx at most twice. Honor numeric `Retry-After` up to 60 seconds; tests inject a
  sleeper and never sleep in real time.
- Reject authenticated redirects. Reject all delivery redirects.
- A failed range upload is resumable only by rereading the reserved file resource and using
  still-valid operations; expired or inconsistent operations create a fresh upload run rather
  than guessing.
- Apple validation/import failures are terminal for that artifact but preserve sanitized state
  details for the operator.

## 13. CLI behavior

```bash
storehelper publish --app wallet --store apple --file build/wallet.ipa --dry-run
storehelper publish --app wallet --store apple --file build/wallet.ipa --no-submit
storehelper publish --app wallet --store apple --file build/wallet.ipa \
  --release-notes-file RELEASE_NOTES.md
storehelper status --app wallet --store apple
storehelper resume RUN_ID
```

`--dry-run` performs IPA/configuration validation with zero network and credentials. `--no-submit`
uploads and waits for a processed Build but does not attach it to the App Store version or edit
metadata. Full submission retains interactive confirmation; JSON/CI requires `--yes`.

## 14. Security boundaries

- `.p8` material lives only in keyring, CI secret file, complete CI variables, or secure prompt.
- Configuration rejects private-key, token, password, issuer-secret, and upload-operation fields.
- Delivery hosts receive only exact Apple-signed headers and their declared byte range.
- File ranges are streamed; the full IPA is never loaded into memory.
- Receipts allowlist public IDs and hashes. Private keys, JWTs, asset tokens, URLs, headers,
  cookies, and raw error documents are forbidden.
- Automated tests never call Apple. Live tests begin with dry-run, then no-submit, and require a
  separate human decision before review submission.

## 15. Testing and completion

- Unit tests: credentials, ES256 team/individual JWTs, IPA parsing, range-plan validation,
  JSON:API error sanitization, configuration, registry, receipt migration, and status mapping.
- Mocked integration tests: app/version verification, multi-range upload and commit, processing
  success/failure, build attachment, localization-only update, review reconciliation/submission,
  timeout/resume, interruption, and no duplicate upload.
- Cross-store regression: all Huawei Android and HarmonyOS tests remain unchanged in behavior.
- Security regression: known private keys, JWTs, upload URLs/headers, asset tokens, and raw Apple
  payload markers are absent from text, JSON, exceptions, and receipt files.
- Final gates: Ruff, mypy, at least 90% coverage, source/wheel build, Twine check, isolated wheel
  install, and installed Huawei/HarmonyOS/Apple dry-runs.
