# StoreHelper

Local-first app store publishing for developers, CI/CD, and AI agents.

StoreHelper 是一个本地优先的应用市场发布 CLI 和 Python SDK。`v0.7.0` 支持将 Android
APK/AAB、HarmonyOS APP/HAP 和 iOS IPA 发布到已有的华为 AppGallery Connect、Apple
App Store Connect、Google Play、小米、OPPO 或 vivo 软件商店应用，并提供安全的状态查询、
断点恢复或原子/分阶段提交保护。

> Status: alpha. Store records and release versions must already exist. StoreHelper does not
> build/sign artifacts or create apps, certificates, versions, pricing, privacy, or rollout policy.

## Why StoreHelper?

- One command validates, uploads, prepares a release, and optionally submits it for review on
  Huawei Android, HarmonyOS, Apple, Google Play, Xiaomi, OPPO, or vivo.
- Credentials live in the OS keyring or CI secret store—not in project YAML.
- Every durable step has a redacted atomic receipt, so compilation timeouts can be resumed
  without uploading the package again.
- Text output is human-friendly; `--output json` is stable for CI/CD and MCP consumers.
- A store-neutral Python core and audited static adapter registry are ready for future stores.

## Requirements and installation

- Python 3.11 or newer
- An existing target in AppGallery Connect or Google Play, or an existing iOS app and editable
  App Store version in App Store Connect
- Huawei: a Service Account JSON with `key_id`, `sub_account`, and an unencrypted RSA private key
- Apple: a team or individual App Store Connect API key with its unencrypted P-256 `.p8` key
- Google Play: a dedicated Google service account with app access in Play Console and its
  standard unencrypted RSA key JSON
- Xiaomi: an existing package, automatic-publishing API secret, Xiaomi X.509 RSA public
  certificate, a signed APK, and a PNG icon
- OPPO: an existing mainland-China application, its application-specific Open Platform
  `client_id`/`client_secret`, and one signed APK with a new version code
- vivo: an existing mainland-China application, publishing API `access_key`/`secret_key`, and one
  signed APK with a new version code

Install the isolated CLI with [pipx](https://pipx.pypa.io/):

```bash
pipx install storehelper
storehelper version
```

For development from a clone:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
storehelper --help
```

Homebrew is planned after the first tagged PyPI release. The future formula will live in a
separate `StoreHelper/homebrew-tap` repository.

## Quick start

Create a secret-free project configuration:

```bash
storehelper init
```

Edit `storehelper.yaml`. One logical app may configure any subset of the seven stores. Android and
Google Play use the application-level package name; HarmonyOS and Apple have their own
package/bundle identifiers.

编辑 `storehelper.yaml`。同一个应用别名可以配置华为 Android、HarmonyOS、Apple、Google
Play、小米、OPPO 和 vivo 软件商店中的任意组合；示例中的 ID、包名和配置名都是假的。

```yaml
version: 1

apps:
  my-android-app:
    package_name: com.example.app
    stores:
      huawei:
        app_id: "123456789"
        credential_profile: default
        language: zh-CN
      harmonyos:
        app_id: "987654321"
        package_name: com.example.app.harmony
        credential_profile: default
        language: zh-CN
      apple:
        app_id: "1234567890"
        bundle_id: com.example.app.ios
        app_store_version_id: 11111111-2222-3333-4444-555555555555
        credential_profile: apple-release
        platform: IOS
        language: en-US
      google_play:
        credential_profile: google-release
        track: internal
        release_status: draft
        language: en-US
      xiaomi:
        credential_profile: xiaomi-release
        app_name: Example App
        icon: assets/xiaomi-icon.png
        privacy_url: https://example.com/privacy
        language: zh-CN
      oppo:
        credential_profile: oppo-release
        version_code: 123
        language: zh-CN
      vivo:
        credential_profile: vivo-release
        version_code: 124
        language: zh-CN
```

Both adapters use the same Huawei Service Account credential format, so they may share one
keyring profile when the account can access both apps. Import it once and verify each target:

```bash
storehelper credentials import --profile default --file ~/Downloads/huawei-service-account.json
storehelper credentials list
storehelper credentials verify --app my-android-app --store huawei
storehelper credentials verify --app my-android-app --store harmonyos
```

Apple credentials use a separate keyring namespace. Wrap the downloaded `.p8` value in a local
JSON file (never commit it), then import and verify it:

```json
{
  "key_type": "team",
  "key_id": "EXAMPLE123",
  "issuer_id": "00000000-0000-0000-0000-000000000000",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
}
```

```bash
storehelper credentials import \
  --store apple --profile apple-release --file ~/Downloads/apple-api-key.json
storehelper credentials list --store apple
storehelper credentials verify --app my-android-app --store apple
```

For an individual API key, set `key_type` to `individual` and omit `issuer_id`.

Google Play uses its own keyring namespace. Import the standard service-account JSON downloaded
for a dedicated account that has access to the existing Play Console app:

```bash
storehelper credentials import \
  --store google_play --profile google-release --file ~/Downloads/google-service-account.json
storehelper credentials list --store google_play
storehelper credentials verify --app my-android-app --store google_play
```

Xiaomi also uses an independent keyring namespace. Obtain the automatic-publishing API secret
and Xiaomi public certificate from the developer console. The API secret is not the interactive
login password. Import a local JSON file that is never committed:

```json
{
  "username": "developer@example.com",
  "api_secret": "replace-with-the-generated-api-secret",
  "public_key_certificate": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n"
}
```

Optional reviewer accounts belong in the same credential file under `test_accounts`; see the
[Xiaomi live checklist](docs/XIAOMI_MANUAL_TEST.md) for the structured format and limits.

```bash
storehelper credentials import \
  --store xiaomi --profile xiaomi-release --file ~/Downloads/xiaomi-api.json
storehelper credentials list --store xiaomi
storehelper credentials verify --app my-android-app --store xiaomi
```

OPPO uses an application-specific credential pair. Create an API client for the existing app in
the OPPO Open Platform, keep the pair in a local JSON file outside the repository, and import it
into the independent OPPO keyring namespace:

```json
{
  "client_id": "replace-with-oppo-client-id",
  "client_secret": "replace-with-oppo-client-secret"
}
```

```bash
storehelper credentials import \
  --store oppo --profile oppo-release --file ~/Downloads/oppo-api.json
storehelper credentials list --store oppo
storehelper credentials verify --app my-android-app --store oppo
```

The profile is application-specific. Do not reuse it for a different OPPO package. See the
[OPPO live checklist](docs/OPPO_MANUAL_TEST.md) before any real submission.

vivo uses an account-level publishing API pair in its own keyring namespace. Store both values in
a local JSON file outside the repository, import it, and verify the configured existing package:

```json
{
  "access_key": "replace-with-vivo-access-key",
  "secret_key": "replace-with-vivo-secret-key"
}
```

```bash
storehelper credentials import \
  --store vivo --profile vivo-release --file ~/Downloads/vivo-api.json
storehelper credentials list --store vivo
storehelper credentials verify --app my-android-app --store vivo
```

Both values are secrets even though one is named `access_key`. See the
[vivo live checklist](docs/VIVO_MANUAL_TEST.md) before any real submission.

Validate locally without credentials or network access:

```bash
storehelper publish --app my-android-app --file build/app-release.aab --dry-run
storehelper publish --app my-android-app --store harmonyos --file build/wallet.app --dry-run
storehelper publish --app my-android-app --store apple --file build/Wallet.ipa --dry-run
storehelper publish --app my-android-app --store google_play --file build/app-release.aab --dry-run
storehelper publish --app my-android-app --store xiaomi --file build/app-release.apk --dry-run
storehelper publish --app my-android-app --store oppo --file build/app-release.apk --dry-run
storehelper publish --app my-android-app --store vivo --file build/app-release.apk --dry-run
```

Upload and wait for package readiness without changing metadata or submitting review:

```bash
storehelper publish --app my-android-app --file build/app-release.aab --no-submit
storehelper publish --app my-android-app --store harmonyos --file build/wallet.hap --no-submit
storehelper publish --app my-android-app --store apple --file build/Wallet.ipa --no-submit
storehelper publish --app my-android-app --store google_play --file build/app-release.aab --no-submit
```

Xiaomi, OPPO, and vivo do not support `--no-submit`. Xiaomi uploads and submits in one request;
OPPO and vivo keep their temporary upload values only in memory for the immediately following
final submission. Use `--dry-run` for zero-network validation.

Publish end to end:

```bash
storehelper publish \
  --app my-android-app \
  --store huawei \
  --file build/app-release.aab \
  --release-notes-file RELEASE_NOTES.md
```

Publish a HarmonyOS APP or HAP through the same state machine:

```bash
storehelper publish \
  --app my-android-app \
  --store harmonyos \
  --file build/wallet.app \
  --release-notes "修复已知问题"
```

Publish an IPA through Apple's native Build Upload API. Release notes are optional for Apple;
when supplied, StoreHelper updates only `whatsNew` for the configured locale:

```bash
storehelper publish \
  --app my-android-app \
  --store apple \
  --file build/Wallet.ipa \
  --release-notes-file RELEASE_NOTES.md
```

Publish an AAB or APK through a Google Play App Edit. `draft` is recommended for the first live
test. `completed` is an explicit target configuration choice and may send the release for review
or make it available according to Play Console policy:

```bash
storehelper publish \
  --app my-android-app \
  --store google_play \
  --file build/app-release.aab \
  --release-notes "Improved stability"
```

Google release notes are optional and limited to 500 characters when supplied. StoreHelper
refuses to overwrite an active staged or halted rollout and never edits unrelated tracks,
country targeting, rollout fractions, or update priority.

Publish an existing Xiaomi APK update. Release notes are required and become `updateDesc`; the
configured icon is resolved relative to `storehelper.yaml` and uploaded with the APK:

```bash
storehelper publish \
  --app my-android-app \
  --store xiaomi \
  --file build/app-release.apk \
  --release-notes "修复已知问题"
```

Xiaomi has no sandbox, upload-only operation, or automatic review-status API. StoreHelper first
uses the read-only query API to verify package ownership/update permission, then sends exactly one
confirmed upload-and-submit request. It supports existing-app, single-APK phone updates only.

Publish an existing OPPO APK update. `version_code` is configured in public YAML and must be
greater than the current OPPO version. Release notes are required (1–500 characters). StoreHelper
reuses the app name, categories, descriptions, privacy URL, icon, screenshots, age/copyright, and
business contact fields returned by OPPO; it does not silently replace missing listing data.

```bash
storehelper publish \
  --app my-android-app \
  --store oppo \
  --file build/app-release.apk \
  --release-notes "修复已知问题"
```

The supported OPPO scope is an existing mainland-China application, one signed APK no larger
than 2 GiB, and a full online update. StoreHelper verifies the package and current version,
allocates and streams one temporary upload, then performs one separately confirmed final review
submission. It never creates or claims an app, changes listing metadata, uploads AAB/multiple APKs,
or schedules a release.

Publish an existing vivo APK update. Configure a positive `version_code` greater than the current
vivo version and supply release notes containing 5–200 characters:

```bash
storehelper publish \
  --app my-android-app \
  --store vivo \
  --file build/app-release.apk \
  --release-notes "修复已知问题并提升稳定性"
```

The v0.7.0 vivo scope is one signed APK no larger than 3 GiB for an existing mainland-China phone
application, with immediate publication after approval. StoreHelper queries the exact package,
streams one temporary upload, and sends one separately confirmed final update containing only the
package/version, upload reference, phone/immediate-online flags, and release notes. It never
creates an app, uploads AAB/multiple APKs, changes listing metadata, or schedules publication.

Interactive text mode shows a confirmation. CI and JSON mode must supply `--yes`:

```bash
storehelper publish \
  --app my-android-app \
  --file build/app-release.aab \
  --release-notes "Improved stability" \
  --yes --output json
```

## Timeout and recovery

Stores process packages asynchronously. StoreHelper polls every 15 seconds for up to 10 minutes.
A timeout exits with code `6`, preserves only durable public identifiers (`pkgVersion`,
`packageId`, Apple's Build Upload/Build IDs, or Google's App Edit ID/`versionCode`), and prints a
safe resume command:

```bash
storehelper resume 20260730T100000Z-a1b2c3d4 --app my-android-app
storehelper runs list
storehelper runs show 20260730T100000Z-a1b2c3d4 --output json
storehelper status --app my-android-app --store harmonyos
storehelper status --app my-android-app --store apple
storehelper status --app my-android-app --store google_play
storehelper status --app my-android-app --store oppo
storehelper status --app my-android-app --store vivo
```

`resume` derives the store from the receipt, verifies the current local artifact digest, starts
from the earliest safe durable step, and does not re-upload a package that was already bound.

Xiaomi is deliberately different. If cancellation, process failure, or network loss occurs after
`submission_started`, the receipt remains `submission_started` or `submission_uncertain`; it is
not resumable and the same app/artifact is blocked. Inspect the Xiaomi console first. Only after
deciding whether another submission is safe should you acknowledge the result with
`storehelper runs delete RUN_ID` and run a newly confirmed publish. `status --store xiaomi` is
unsupported because Xiaomi does not expose that API.

OPPO is also non-resumable, but uses a two-phase in-memory safety boundary. Failures before the
final submission are safe for a newly confirmed run. Once the receipt reaches
`submission_started`, cancellation, process loss, or a missing response becomes
`submission_uncertain` and blocks the same app/artifact. Query `status --store oppo`, inspect the
OPPO console, and delete the local receipt only after a release owner deliberately decides whether
another submission is safe. Receipt deletion never changes remote OPPO state.

vivo follows the same staged, non-resumable boundary. An upload failure before
`submission_started` is safe for a newly confirmed run. After the final update begins, a missing
response or interruption becomes `submission_uncertain` and blocks the same app/artifact. Query
`status --store vivo`, inspect the vivo console, and delete only the local receipt after the
release owner determines whether a new submission is safe.

## CI credentials

Use one of these mutually exclusive forms:

```bash
export STOREHELPER_HUAWEI_CREDENTIALS_FILE="$RUNNER_TEMP/huawei-service-account.json"
```

or:

```bash
export STOREHELPER_HUAWEI_KEY_ID="..."
export STOREHELPER_HUAWEI_SUB_ACCOUNT="..."
export STOREHELPER_HUAWEI_PRIVATE_KEY="..."
```

For Apple, use either a secret JSON file:

```bash
export STOREHELPER_APPLE_CREDENTIALS_FILE="$RUNNER_TEMP/apple-api-key.json"
```

or the complete variable set:

```bash
export STOREHELPER_APPLE_KEY_TYPE="team"
export STOREHELPER_APPLE_KEY_ID="..."
export STOREHELPER_APPLE_ISSUER_ID="..."
export STOREHELPER_APPLE_PRIVATE_KEY="..."
```

Omit `STOREHELPER_APPLE_ISSUER_ID` only for an individual key. Do not mix file and individual
variables, store credentials in `storehelper.yaml`, or pass private keys on the command line.

For Google Play, use either the standard JSON file:

```bash
export STOREHELPER_GOOGLE_CREDENTIALS_FILE="$RUNNER_TEMP/google-service-account.json"
```

or the complete variable set:

```bash
export STOREHELPER_GOOGLE_PROJECT_ID="..."
export STOREHELPER_GOOGLE_PRIVATE_KEY_ID="..."
export STOREHELPER_GOOGLE_PRIVATE_KEY="..."
export STOREHELPER_GOOGLE_CLIENT_EMAIL="..."
```

`STOREHELPER_GOOGLE_TOKEN_URI` is optional and normally remains the official Google OAuth token
endpoint. Do not mix the Google file and individual variable forms.

For Xiaomi, use either one secret JSON file:

```bash
export STOREHELPER_XIAOMI_CREDENTIALS_FILE="$RUNNER_TEMP/xiaomi-api.json"
```

or the complete base variable set, with optional structured reviewer JSON:

```bash
export STOREHELPER_XIAOMI_USERNAME="developer@example.com"
export STOREHELPER_XIAOMI_API_SECRET="..."
export STOREHELPER_XIAOMI_PUBLIC_KEY_CERTIFICATE="..."
export STOREHELPER_XIAOMI_TEST_ACCOUNTS_JSON='{"zh_CN":{"accounts":[],"audit_notes":"Review instructions"}}'
```

Do not mix file and individual Xiaomi sources. Avoid putting secrets in shell history; a CI secret
file is usually safer for the multiline certificate and nested reviewer values.

For OPPO, use either one application-specific secret JSON file:

```bash
export STOREHELPER_OPPO_CREDENTIALS_FILE="$RUNNER_TEMP/oppo-api.json"
```

or the complete pair:

```bash
export STOREHELPER_OPPO_CLIENT_ID="..."
export STOREHELPER_OPPO_CLIENT_SECRET="..."
```

Do not mix the OPPO file and individual variables, and do not pass either value on the command
line. Each application should use a dedicated OPPO profile.

For vivo, use either one secret JSON file:

```bash
export STOREHELPER_VIVO_CREDENTIALS_FILE="$RUNNER_TEMP/vivo-api.json"
```

or the complete pair:

```bash
export STOREHELPER_VIVO_ACCESS_KEY="..."
export STOREHELPER_VIVO_SECRET_KEY="..."
```

Do not mix the vivo file and individual variables or pass either value on the command line.

Temporary upload URLs, signed headers, object IDs, JWTs, and raw vendor responses are never
persisted. See [the security guide](docs/SECURITY_GUIDE.md).

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Success |
| 2 | Invalid usage or configuration |
| 3 | Credential or authentication failure |
| 4 | Package validation failure |
| 5 | App store/vendor rejection |
| 6 | Resumable compilation timeout |
| 7 | Corrupt or unavailable local run state |
| 8 | Network failure after bounded retries |
| 130 | Interrupted |

## Store troubleshooting

- `204144662`: the package could not be bound. StoreHelper uses one sanitized logical filename
  (maximum 64 characters) for both upload and binding to avoid the common `fileName` mismatch.
- `204144727`: Huawei is still compiling. StoreHelper polls before submission and treats this as
  resumable eventual consistency.
- `204144735`: Huawei is still performing security detection; resume after the timeout.
- `401/403` or `204144665`: verify the Service Account, app access, system time, and configured
  `app_id`/package name.
- `HARMONYOS_APP_NOT_FOUND`: verify the HarmonyOS `app_id`, store-local `package_name`, and that
  the Service Account can see a package type `7` application.
- `APPLE_BUNDLE_MISMATCH` or `APPLE_VERSION_MISMATCH`: the IPA bundle ID or marketing version
  differs from the configured existing App Store version.
- `APPLE_AUTHORIZATION_FAILED`: verify API-key type, key ID, issuer ID rules, role/app access, and
  system time.
- `APPLE_LOCALIZATION_NOT_UNIQUE`: the configured locale must match exactly one localization when
  release notes are supplied; omit release notes if no `whatsNew` update is intended.
- `GOOGLE_ACTIVE_ROLLOUT`: the selected track already contains an `inProgress` or `halted`
  rollout; resolve it deliberately in Play Console rather than overwriting it.
- `GOOGLE_CHANGES_IN_REVIEW`: another Edit is already in review; wait for that review to finish,
  then resume the run.
- `GOOGLE_EDIT_EXPIRED`: the uncommitted App Edit no longer exists; start a new publish. A Google
  `--no-submit` Edit is intentionally temporary and cannot be treated as a permanent draft.
- `GOOGLE_AUTHORIZATION_FAILED`: verify that the Android Publisher API is enabled, the service
  account has app access in Play Console, and the configured package/track are correct.
- `XIAOMI_PACKAGE_CLAIM_REQUIRED`: the package belongs to another developer account; complete the
  claim in Xiaomi's console before retrying.
- `XIAOMI_UPDATE_NOT_ALLOWED`: Xiaomi's read-only query currently disallows a version update;
  inspect package/review state in the console.
- `XIAOMI_SIGNATURE_REJECTED` or `XIAOMI_AUTHENTICATION_FAILED`: verify the generated API secret,
  current Xiaomi public certificate, developer email, and whether the secret was reset.
- `submission_uncertain`: do not retry automatically. Inspect the Xiaomi console and delete only
  the local receipt after a release owner makes a deliberate retry decision.
- `OPPO_APPLICATION_INCOMPLETE`: complete the existing OPPO listing in the developer console;
  StoreHelper will not invent required categories, descriptions, media, privacy, copyright, or
  business contact values.
- `OPPO_VERSION_CONFLICT` or `OPPO_VERSION_EXISTS`: set a new positive `version_code` greater than
  the current OPPO version and confirm the signed APK uses that version.
- `OPPO_UPLOAD_HOST_UNSAFE`: stop and re-check the official OPPO service. StoreHelper accepts only
  HTTPS upload URLs under its explicit OPPO/HeyTap allowlist and never follows redirects.
- OPPO `submission_uncertain`: query OPPO status and inspect the console before deleting the local
  receipt or authorizing another submission.
- `VIVO_VERSION_CONFLICT`: set a positive `version_code` greater than the current vivo version and
  confirm that the signed APK contains the same package and intended version.
- `VIVO_UPDATE_CONFLICT` (vendor `B0302`): vivo already has an update in progress; reconcile it in
  the vivo console before starting another publish.
- vivo `submission_uncertain`: query vivo status and inspect the console before deleting the local
  receipt or authorizing another submission. StoreHelper never retries the final mutation.

For the deliberately opt-in production checklist, see
[`docs/HARMONYOS_MANUAL_TEST.md`](docs/HARMONYOS_MANUAL_TEST.md) or
[`docs/APPLE_MANUAL_TEST.md`](docs/APPLE_MANUAL_TEST.md), or
[`docs/GOOGLE_PLAY_MANUAL_TEST.md`](docs/GOOGLE_PLAY_MANUAL_TEST.md), or
[`docs/XIAOMI_MANUAL_TEST.md`](docs/XIAOMI_MANUAL_TEST.md), or
[`docs/OPPO_MANUAL_TEST.md`](docs/OPPO_MANUAL_TEST.md), or
[`docs/VIVO_MANUAL_TEST.md`](docs/VIVO_MANUAL_TEST.md). Start with local-only `--dry-run` and
read-only credential verification. Use `--no-submit` only where supported; only a separately
confirmed command should commit or submit a review.

## Development

```bash
ruff format --check .
ruff check .
mypy src
pytest --cov=storehelper --cov-report=term-missing --cov-fail-under=90
python -m build
python -m twine check dist/*
```

Architecture and implementation progress are tracked in
[`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md). Release maintainers should also read
[`docs/RELEASING.md`](docs/RELEASING.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).
