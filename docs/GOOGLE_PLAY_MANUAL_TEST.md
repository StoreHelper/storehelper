# Google Play live verification / Google Play 线上验证

This checklist is deliberately opt-in. Automated tests use generated keys, fake identifiers, and
mocked HTTP only; they never contact Google, upload an artifact, update a track, or commit an App
Edit.

本文档中的线上步骤必须由操作者主动执行。自动化测试不会连接 Google、上传真实安装包、更新
Track 或提交 App Edit。

Authoritative references:

- [Google Play Developer API setup](https://developers.google.com/android-publisher/getting_started)
- [OAuth 2.0 for service accounts](https://developers.google.com/identity/protocols/oauth2/service-account)
- [App edits](https://developers.google.com/android-publisher/api-ref/rest/v3/edits)
- [AAB upload](https://developers.google.com/android-publisher/api-ref/rest/v3/edits.bundles/upload)
- [APK upload](https://developers.google.com/android-publisher/api-ref/rest/v3/edits.apks/upload)
- [Tracks](https://developers.google.com/android-publisher/api-ref/rest/v3/edits.tracks)
- [Commit behavior](https://developers.google.com/android-publisher/api-ref/rest/v3/edits/commit)
- [Release lifecycle](https://developers.google.com/android-publisher/api-ref/rest/v3/applications.tracks.releases)

## 1. Prepare Google Cloud and Play Console

- Use an application that already exists in Play Console and has completed Google's required
  one-time/manual setup. StoreHelper does not create the app or its listings.
- Enable the Google Play Android Developer API in the linked Google Cloud project.
- Create a dedicated service account. In Play Console, grant it access only to the test app and
  only the release/read permissions required for the tracks you will exercise. Do not begin with
  organization-wide administrator access.
- Download one standard service-account JSON key according to company policy. Treat it as a
  long-lived secret; never paste it into YAML, shell history, an issue, or a build log.
- Prepare a signed `.aab` (recommended) or `.apk` with the exact existing package name and a new,
  positive `versionCode`. StoreHelper validates the archive locally; Google remains authoritative
  for package name, signing identity, manifest, and version-code checks.
- Resolve any existing `inProgress` or `halted` rollout on the selected track before testing.
  StoreHelper intentionally refuses to overwrite it.

## 2. Configure a safe draft target

Every value below is fake. Google uses the application-level `package_name` as its application ID:

```yaml
version: 1
apps:
  manual-google-test:
    package_name: com.example.wallet
    stores:
      google_play:
        credential_profile: manual-google
        track: internal
        release_status: draft
        language: en-US
```

`track` is required. Start with an internal or other non-production test track. Keep
`release_status: draft` for the first committed verification. `language` is a BCP-47 tag and is
used only when release notes are supplied.

```bash
storehelper config validate --config storehelper.yaml
```

## 3. Import credentials safely

Import the original standard Google JSON directly into the dedicated Google keyring namespace:

```bash
storehelper credentials import \
  --store google_play \
  --profile manual-google \
  --file ~/Downloads/google-service-account.json

storehelper credentials list --store google_play
```

After confirming keyring storage, remove temporary plaintext copies according to company policy.
For CI, use either `STOREHELPER_GOOGLE_CREDENTIALS_FILE` or the complete
`STOREHELPER_GOOGLE_PROJECT_ID`, `STOREHELPER_GOOGLE_PRIVATE_KEY_ID`,
`STOREHELPER_GOOGLE_PRIVATE_KEY`, and `STOREHELPER_GOOGLE_CLIENT_EMAIL` set. Do not mix both forms.
The optional `STOREHELPER_GOOGLE_TOKEN_URI` normally remains
`https://oauth2.googleapis.com/token`.

## 4. Run local-only validation first

This validates configuration, archive structure, suffix, size, logical filename, and SHA-256. It
performs zero credential or network operations:

```bash
storehelper publish \
  --app manual-google-test \
  --store google_play \
  --file build/app-release.aab \
  --dry-run \
  --output json \
  --config storehelper.yaml
```

Confirm exit code `0`, `"store":"google_play"`, and `"stage":"completed"`.

## 5. Verify read-only access

```bash
storehelper credentials verify \
  --app manual-google-test \
  --store google_play \
  --config storehelper.yaml

storehelper status \
  --app manual-google-test \
  --store google_play \
  --output json \
  --config storehelper.yaml
```

Both commands use the read-only release-lifecycle endpoint. They do not create an App Edit. Stop
if package, track, service-account access, or authorization is wrong.

## 6. Test upload without changing a track

This step creates an App Edit and uploads the artifact. It does not update the track, validate the
Edit, or commit it:

```bash
storehelper publish \
  --app manual-google-test \
  --store google_play \
  --file build/app-release.aab \
  --no-submit \
  --output json \
  --config storehelper.yaml
```

Confirm `"stage":"package_ready"`. The receipt may contain the public App Edit ID and
`versionCode`; it must not contain the service-account key, OAuth assertion, access token, request
headers, or raw Google response.

An uncommitted App Edit expires at Google's `expiryTimeSeconds`. `--no-submit` is an integration
check, not a permanent Play Console draft, and its terminal receipt cannot later commit that Edit.
A subsequent full publish creates a new Edit and performs another upload.

## 7. Commit a draft release

Review the package, target track, receipt, and Play Console state. Then use a separately approved
command while `release_status` remains `draft`:

```bash
storehelper publish \
  --app manual-google-test \
  --store google_play \
  --file build/app-release.aab \
  --release-notes "StoreHelper draft verification" \
  --config storehelper.yaml
```

Interactive text mode asks for confirmation. Non-interactive and JSON usage require `--yes`:

```bash
storehelper publish \
  --app manual-google-test \
  --store google_play \
  --file build/app-release.aab \
  --release-notes-file RELEASE_NOTES.md \
  --yes --output json \
  --config storehelper.yaml
```

Release notes are optional; when present they must contain 1–500 characters. StoreHelper updates
only the configured track, sends only the version code/status/one locale note it owns, validates
the Edit, and commits with `ERROR_IF_IN_REVIEW`. It never cancels another review automatically.

## 8. Treat `completed` as a separate production decision

Only after the draft flow is reviewed should a release owner consider changing the configuration:

```yaml
release_status: completed
```

Use a correctly signed artifact with an unused higher `versionCode`, re-run `--dry-run` and
read-only verification, and obtain the required production approval before executing the full
publish command. A completed release may be submitted for review or become available according
to the selected track and Play Console/managed-publishing policy.

StoreHelper does not configure staged rollout, country targeting, device tiers, update priority,
managed publishing, listings, screenshots, pricing, data safety, or production rollback.

## 9. Recovery and status

After a recoverable Track or Commit response loss, StoreHelper returns exit code `6` and a safe
resume command. Keep the original artifact and target configuration unchanged:

```bash
storehelper resume RUN_ID \
  --app manual-google-test \
  --output json \
  --config storehelper.yaml
```

Resume verifies the local SHA-256, package, track, and release status before network I/O. Once the
Edit ID and `versionCode` are durable, it retries only the idempotent Track/Commit stage and does
not upload the artifact again. If the Commit response was lost, lifecycle reconciliation treats a
visible version as success.

- `GOOGLE_CHANGES_IN_REVIEW`: wait for the existing review to finish, then resume.
- `GOOGLE_EDIT_EXPIRED`: the Edit is absent and the version is not visible; start a new publish.
- `GOOGLE_EDIT_CONFLICT`: another update invalidated this Edit; review Play Console and start a
  new publish rather than forcing a retry.

Check lifecycle status without creating an Edit:

```bash
storehelper status \
  --app manual-google-test \
  --store google_play \
  --output json \
  --config storehelper.yaml
```

## 10. Cleanup and incident handling

- `storehelper runs delete RUN_ID` deletes only the local receipt; it never changes Google Play.
- Allow abandoned uncommitted Edits to expire, or follow approved Play Console cleanup policy.
- If a JSON key, private key, assertion, or access token was exposed, stop publishing, disable or
  delete the affected key, remove leaked logs/artifacts, create a replacement key, and follow the
  organization's incident process.
- Record the exact artifact digest, track, status, command approval, and Play Console result in the
  release evidence required by your organization.
