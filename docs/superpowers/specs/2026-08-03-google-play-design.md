# Google Play Publishing Design

**Status:** Approved under the maintainer's standing authorization to use the recommended design
**Date:** 2026-08-03
**Target release:** v0.4.0

## 1. Context

StoreHelper v0.3.0 publishes Huawei Android, HarmonyOS, and Apple artifacts through a shared,
local-first state machine. The next increment adds Google Play for an existing Android
application, using the Google Play Developer Publishing API directly and without invoking Gradle,
Fastlane, or vendor command-line tools.

Authoritative references used for this design:

- [Google Play Developer API getting started](https://developers.google.com/android-publisher/getting_started)
- [Service-account OAuth 2.0](https://developers.google.com/identity/protocols/oauth2/service-account)
- [Google Play Developer API v3](https://developers.google.com/android-publisher/api-ref/rest)
- [App edits](https://developers.google.com/android-publisher/api-ref/rest/v3/edits)
- [Bundle uploads](https://developers.google.com/android-publisher/api-ref/rest/v3/edits.bundles/upload)
- [APK uploads](https://developers.google.com/android-publisher/api-ref/rest/v3/edits.apks/upload)
- [Tracks and releases](https://developers.google.com/android-publisher/api-ref/rest/v3/edits.tracks)
- [Commit behavior](https://developers.google.com/android-publisher/api-ref/rest/v3/edits/commit)
- [Release lifecycle status](https://developers.google.com/android-publisher/api-ref/rest/v3/applications.tracks.releases)

## 2. Goals

- Add `--store google_play` for an existing Google Play application.
- Support signed `.aab` and `.apk` files already produced by the user's build pipeline.
- Use a Google service-account JSON key stored in a separate credential namespace.
- Generate RS256 service-account assertions and exchange them for scoped OAuth access tokens.
- Verify package/track access with the read-only release-lifecycle endpoint.
- Create an App Edit, stream one artifact through the media upload endpoint, and verify Google's
  returned SHA-256 and `versionCode`.
- Update one configured track with a `draft` or `completed` release and optional localized notes.
- Validate and commit the edit without canceling changes already in review.
- Recover safely from upload, track-update, and commit response loss.
- Query the current review lifecycle without creating an Edit.
- Keep all automated tests offline and all live mutations explicitly operator initiated.

## 3. Non-goals

- Creating a Play Console application or performing its first manual setup.
- Building, signing, zip-aligning, or generating an Android App Bundle.
- Staged rollout (`inProgress`), halted rollout, country targeting, update priority, device tiers,
  managed publishing toggles, store listings, screenshots, pricing, or data-safety declarations.
- Multiple APK variants in one StoreHelper command.
- Automatically canceling an existing review or staged rollout.
- Automatically sending changes that Play has placed in `NOT_SENT_FOR_REVIEW`; that remains an
  explicit Play Console action.
- Hosting Real-time Developer Notifications; that belongs in a future service/MCP package.

## 4. Selected workflow

```text
local validation
  → read-only lifecycle access check
  → edits.insert
  → streamed AAB/APK media upload
  → verify returned versionCode + SHA-256
  → edits.tracks.get
  → safe edits.tracks.update
  → edits.validate
  → lifecycle reconciliation
  → edits.commit(ERROR_IF_IN_REVIEW)
  → lifecycle reconciliation/status
```

`--dry-run` stops after local validation and performs zero credential or network operations.
`--no-submit` creates an Edit and uploads the artifact, then stops without updating a track or
committing the Edit. The Edit is temporary and expires at Google's `expiryTimeSeconds`; this mode
is intended for integration validation, not as a permanent Play Console draft.

## 5. Configuration

Configuration schema version remains `1`. Google uses the application-level Android package name:

```yaml
version: 1
apps:
  wallet:
    package_name: com.example.wallet
    stores:
      google_play:
        credential_profile: google-release
        track: internal
        release_status: draft
        language: en-US
```

`track` is required and must be a safe Google track identifier. `release_status` is deliberately
required and limited to `draft` or `completed`; no implicit production rollout exists. `draft` is
the recommended first live test. Selecting `completed` may send changes for review and may make an
approved release available according to Play Console settings.

Resolved targets add explicit optional `track` and `release_status` fields. Receipt schema v4 adds
the same fields plus `operation_id`, which stores only the public App Edit ID. Migration from v1,
v2, and v3 fills new fields with `None`; Huawei, HarmonyOS, and Apple behavior is unchanged.

## 6. Credentials and authentication

Google credentials use `CredentialKind.GOOGLE_SERVICE_ACCOUNT` and a dedicated keyring namespace.
The imported JSON is the standard service-account key shape, restricted to:

- `type=service_account`;
- `project_id`;
- `private_key_id`;
- `private_key` (unencrypted RSA PEM);
- `client_email`;
- `token_uri` (HTTPS, normally `https://oauth2.googleapis.com/token`).

```bash
storehelper credentials import \
  --store google_play --profile google-release --file service-account.json
```

CI supports either `STOREHELPER_GOOGLE_CREDENTIALS_FILE` or the complete individual variables
`STOREHELPER_GOOGLE_PROJECT_ID`, `STOREHELPER_GOOGLE_PRIVATE_KEY_ID`,
`STOREHELPER_GOOGLE_PRIVATE_KEY`, `STOREHELPER_GOOGLE_CLIENT_EMAIL`, and optional HTTPS
`STOREHELPER_GOOGLE_TOKEN_URI`. File and individual forms are mutually exclusive.

The JWT header uses `alg=RS256`, `typ=JWT`, and `kid=private_key_id`. Claims use
`iss=client_email`, `scope=https://www.googleapis.com/auth/androidpublisher`, `aud=token_uri`, and
a maximum one-hour `iat`/`exp` interval. StoreHelper exchanges the assertion with
`grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`, caches only the in-memory access token,
and renews once after 401.

## 7. Artifact validation and upload

The existing Android ZIP validator remains the common local baseline: regular file, `.aab` or
`.apk`, bounded size, valid non-empty ZIP, no traversal entries, expected manifest/signature
shape, sanitized logical filename, and streamed SHA-256. Google performs authoritative manifest,
signature, package-name, and version-code checks during upload.

The client creates an Edit and uploads with `uploadType=media` to the artifact-specific
`/upload/androidpublisher/v3/.../bundles` or `/apks` endpoint. The request body is streamed from
disk, redirects are disabled, and the bearer token is sent only to the fixed Google API host.
The response must contain a positive `versionCode` and a SHA-256 equal to the local artifact.

The adapter returns:

```python
UploadedArtifact(artifact_id=str(version_code), operation_id=edit_id)
```

An upload interruption before that response may leave an uncommitted Edit, but cannot publish a
release. A retry opens a new Edit; abandoned Edits expire. Once both IDs are persisted, later
recovery never uploads the artifact again.

## 8. Track safety and release notes

Before updating a track, StoreHelper reads it from the same Edit. If any release is `inProgress`
or `halted`, the command stops rather than overwrite an active staged rollout.

- For a configured `draft`, current releases are preserved and one draft release is appended.
- For `completed`, the update contains one completed release with the new version code, matching
  Google's normal full-rollout replacement semantics.
- Release notes are optional. When supplied, StoreHelper sends only one `LocalizedText` entry for
  the configured BCP-47 language and enforces Google's 500-character StoreHelper boundary.
- StoreHelper never modifies country targeting, user fraction, update priority, or unrelated
  tracks.

## 9. Validation, commit, and recovery

Submission requires both persisted `operation_id` (Edit ID) and `artifact_id` (`versionCode`). It:

1. queries the read-only lifecycle endpoint for the configured package/track/version;
2. returns success immediately if that version is already visible, recovering a lost commit
   response;
3. verifies the Edit still exists;
4. calls `edits.validate`;
5. commits with `changesNotSentForReview=false` and
   `changesInReviewBehavior=ERROR_IF_IN_REVIEW`.

The explicit review behavior is a safety boundary: StoreHelper will not use Google's default
`CANCEL_IN_REVIEW_AND_SUBMIT`, which could cancel an unrelated review. A missing Edit is reconciled
against lifecycle status with bounded retries; if the version remains absent, StoreHelper reports
an expired/ambiguous Edit and asks the user to start a new publish rather than guessing.

Network failures after the upload ID is durable preserve `PACKAGE_READY` or `METADATA_UPDATED`, so
`resume` retries the idempotent track or commit step. Receipt target matching rejects a changed
track or release status before network I/O.

## 10. Review status

`status` calls the 2026 read-only endpoint:

```text
GET /androidpublisher/v3/applications/{package}/tracks/{track}/releases
```

Lifecycle mapping:

| Google lifecycle | StoreHelper |
| --- | --- |
| `DRAFT`, `NOT_SENT_FOR_REVIEW` | `pending_review` |
| `IN_REVIEW` | `in_review` |
| `APPROVED_NOT_PUBLISHED`, `PUBLISHED` | `approved` |
| `NOT_APPROVED` | `rejected` |
| missing/unknown/mixed ambiguous state | `unknown` |

When multiple active releases exist, deterministic priority is in-review → rejected → pending →
approved. The status is track-level and does not claim to expose Play Console policy details.

## 11. Errors, retries, and security

- All authenticated URLs are constructed from fixed Google API bases and percent-encoded public
  identifiers; redirects are rejected.
- Token requests allow only the validated HTTPS `token_uri` from the credential.
- 401 refreshes once. 429 and 5xx retry at most twice with bounded `Retry-After`.
- 409 concurrency failures and `ERROR_IF_IN_REVIEW` conflicts are actionable vendor failures, not
  silently retried mutations.
- Google error bodies are reduced to bounded code/status/message fields and passed through secret
  redaction. Request bodies, bearer tokens, assertions, private keys, and raw responses never
  reach output or receipts.
- Receipt schema allowlists only public target, Edit, version, track, hash, and state values.
- Tests use generated keys, fake IDs, and mocked transports only.

## 12. Completion criteria

The v0.4.0 increment is complete only when all existing store regressions pass, Google unit and
integration coverage includes full/draft/no-submit/dry-run/recovery/status/security flows, total
coverage remains at least 90%, package artifacts pass Twine, and the exact wheel completes four-
store installed dry-runs. No automated gate performs a live Google upload or commit.
