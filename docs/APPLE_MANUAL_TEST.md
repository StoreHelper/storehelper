# Apple App Store live verification / Apple App Store 线上验证

This checklist is deliberately opt-in. Automated tests use mocked HTTP only; they never contact
Apple, upload an IPA, attach a Build, change metadata, or submit a version for review.

本文档中的线上步骤必须由操作者主动执行。自动化测试只使用模拟 HTTP，不会连接 Apple、上传
IPA、绑定 Build、修改元数据或提交审核。

StoreHelper uses Apple's native App Store Connect Build Upload API. It does not require Xcode,
Transporter, or `altool`, and it does not build or sign an IPA.

Authoritative references:

- [App Store Connect API keys](https://developer.apple.com/help/app-store-connect/get-started/app-store-connect-api)
- [Generating API request tokens](https://developer.apple.com/documentation/appstoreconnectapi/generating-tokens-for-api-requests)
- [Build uploads](https://developer.apple.com/documentation/appstoreconnectapi/build-uploads)
- [Review submissions](https://developer.apple.com/documentation/appstoreconnectapi/review-submissions)

## 1. Preconditions

- Use an iOS app that already exists in App Store Connect.
- Create the target App Store version before running StoreHelper. StoreHelper needs the version's
  resource ID (`app_store_version_id`), not only a version string such as `1.2.3`.
- Obtain the App resource ID, bundle ID, and App Store version resource ID from App Store Connect
  API responses or approved internal tooling.
- Use a signed `.ipa` whose `CFBundleIdentifier` matches `bundle_id` and whose
  `CFBundleShortVersionString` matches the existing App Store version.
- Use a team or individual App Store Connect API key whose role and app access permit the intended
  build and review operations.
- Never paste the `.p8`, credential JSON, JWT, signed upload URL, upload header, or raw API response
  into project configuration, an issue, or a terminal transcript.

## 2. Configure the existing target

Use your own values locally; every value below is intentionally fake:

```yaml
version: 1
apps:
  manual-apple-test:
    package_name: com.example.placeholder
    stores:
      apple:
        app_id: "1234567890"
        bundle_id: com.example.wallet.ios
        app_store_version_id: 11111111-2222-3333-4444-555555555555
        credential_profile: manual-apple
        platform: IOS
        language: en-US
```

The application-level `package_name` remains required by configuration schema version 1 for
backward compatibility; Apple matching uses `bundle_id`.

Validate the secret-free file:

```bash
storehelper config validate --config storehelper.yaml
```

## 3. Import an API key

StoreHelper imports JSON rather than a raw `.p8`. For a team key, create a local ignored file:

```json
{
  "key_type": "team",
  "key_id": "EXAMPLE123",
  "issuer_id": "00000000-0000-0000-0000-000000000000",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
}
```

For an individual key, use `"key_type": "individual"` and omit `issuer_id`. Both key types must
use the unencrypted P-256 private key downloaded from App Store Connect.

```bash
storehelper credentials import \
  --store apple \
  --profile manual-apple \
  --file ~/Downloads/apple-api-key.json

storehelper credentials list --store apple
```

Delete the temporary plaintext JSON after confirming it is safely stored in the operating-system
keyring. Do not delete the original downloaded key until it is backed up according to your
organization's policy; Apple private keys cannot be downloaded again.

CI may instead set `STOREHELPER_APPLE_CREDENTIALS_FILE`, or the complete
`STOREHELPER_APPLE_KEY_TYPE`, `STOREHELPER_APPLE_KEY_ID`, `STOREHELPER_APPLE_PRIVATE_KEY`, and
team-only `STOREHELPER_APPLE_ISSUER_ID` variables. Do not mix the two source forms.

## 4. Verify read-only access

Run read-only app/version verification first:

```bash
storehelper credentials verify \
  --app manual-apple-test \
  --store apple \
  --config storehelper.yaml
```

Stop if StoreHelper reports an app ID, bundle ID, platform, version relationship, credential, or
authorization mismatch. Verification does not upload or modify App Store Connect resources.

## 5. Local-only IPA dry run

This validates the configuration and IPA ZIP structure, reads the top-level app `Info.plist`,
checks bundle/version metadata, and calculates SHA-256. It performs zero network calls and needs
no credentials:

```bash
storehelper publish \
  --app manual-apple-test \
  --store apple \
  --file build/Wallet.ipa \
  --dry-run \
  --output json \
  --config storehelper.yaml
```

Confirm the JSON result has `"store":"apple"`, `"stage":"completed"`, and exit code `0`.

## 6. Upload and process without review submission

This step changes App Store Connect: it reserves a native Build Upload, transfers the IPA, commits
the file, and waits for Apple to produce a Build. It does not attach the Build to the configured
App Store version, update `whatsNew`, or submit review.

```bash
storehelper publish \
  --app manual-apple-test \
  --store apple \
  --file build/Wallet.ipa \
  --no-submit \
  --config storehelper.yaml
```

Confirm the result reaches `package_ready`, then inspect the redacted receipt:

```bash
storehelper runs list
storehelper runs show RUN_ID --output json
```

The receipt may contain public App, version, Build Upload, and Build IDs. It must not contain a
private key, JWT, upload URL, request header, entity tag, upload operation, or raw Apple response.

## 7. Separately confirmed review submission

Only continue after reviewing the processed Build and configured App Store version. Without
release notes, StoreHelper attaches the Build and creates/reuses the modern review submission:

```bash
storehelper publish \
  --app manual-apple-test \
  --store apple \
  --file build/Wallet.ipa \
  --config storehelper.yaml
```

To also change the configured locale's `whatsNew`, provide 1–4000 characters:

```bash
storehelper publish \
  --app manual-apple-test \
  --store apple \
  --file build/Wallet.ipa \
  --release-notes-file RELEASE_NOTES.md \
  --config storehelper.yaml
```

Interactive text mode asks for confirmation. Non-interactive/JSON usage requires explicit `--yes`:

```bash
storehelper publish \
  --app manual-apple-test \
  --store apple \
  --file build/Wallet.ipa \
  --yes \
  --output json \
  --config storehelper.yaml
```

StoreHelper only attaches the Build, optionally updates one localization's `whatsNew`, and uses
`reviewSubmissions`/`reviewSubmissionItems`. It does not edit screenshots, pricing, availability,
review credentials, export compliance, phased release, or release policy.

## 8. Status, timeout, and recovery

```bash
storehelper status \
  --app manual-apple-test \
  --store apple \
  --output json \
  --config storehelper.yaml
```

If processing or a recoverable network operation exceeds the wait window, StoreHelper exits with
code `6` and prints a resume command. Keep the original IPA unchanged:

```bash
storehelper resume RUN_ID \
  --app manual-apple-test \
  --poll-interval 15s \
  --wait-timeout 10m \
  --output json \
  --config storehelper.yaml
```

Resume derives `apple` from the receipt and verifies the local SHA-256 before network I/O. It
reuses matching upload reservations, skips delivered ranges, polls only an unfinished Build
Upload, and reconciles an existing review draft/item after a lost response.

## 9. Cleanup and incident handling

- `storehelper runs delete RUN_ID` removes only the local receipt; it never changes Apple state.
- Clean up unwanted builds or review drafts in App Store Connect according to company policy.
- If a private key, JWT, or signed delivery value was exposed, stop, revoke/rotate the API key,
  remove leaked logs or artifacts, and follow the organization's incident process.
- StoreHelper v0.3.0 does not automate rollback, withdrawal, phased release, or webhook hosting.
