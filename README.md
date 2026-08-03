# StoreHelper

Local-first app store publishing for developers, CI/CD, and AI agents.

StoreHelper 是一个本地优先的应用市场发布 CLI 和 Python SDK。`v0.3.0` 支持将 Android
APK/AAB、HarmonyOS APP/HAP 和 iOS IPA 发布到已有的华为 AppGallery Connect 或 Apple
App Store Connect 应用，并提供状态轮询和断点恢复。

> Status: alpha. Store records and release versions must already exist. StoreHelper does not
> build/sign artifacts or create apps, certificates, versions, pricing, privacy, or rollout policy.

## Why StoreHelper?

- One command validates, uploads, waits for processing, prepares a release, and optionally
  submits it for review on Huawei Android, HarmonyOS, or Apple.
- Credentials live in the OS keyring or CI secret store—not in project YAML.
- Every durable step has a redacted atomic receipt, so compilation timeouts can be resumed
  without uploading the package again.
- Text output is human-friendly; `--output json` is stable for CI/CD and MCP consumers.
- A store-neutral Python core and audited static adapter registry are ready for future stores.

## Requirements and installation

- Python 3.11 or newer
- An existing target in AppGallery Connect or an existing iOS app and editable App Store version
  in App Store Connect
- Huawei: a Service Account JSON with `key_id`, `sub_account`, and an unencrypted RSA private key
- Apple: a team or individual App Store Connect API key with its unencrypted P-256 `.p8` key

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

Edit `storehelper.yaml`. One logical app may configure any subset of the three stores. Android
keeps its package name at application level for compatibility; HarmonyOS and Apple have their own
package/bundle identifiers.

编辑 `storehelper.yaml`。同一个应用别名可以配置华为 Android、HarmonyOS 和 Apple 中的任意
组合；示例中的 ID、包名和配置名都是假的。

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

Validate locally without credentials or network access:

```bash
storehelper publish --app my-android-app --file build/app-release.aab --dry-run
storehelper publish --app my-android-app --store harmonyos --file build/wallet.app --dry-run
storehelper publish --app my-android-app --store apple --file build/Wallet.ipa --dry-run
```

Upload and wait for package readiness without changing metadata or submitting review:

```bash
storehelper publish --app my-android-app --file build/app-release.aab --no-submit
storehelper publish --app my-android-app --store harmonyos --file build/wallet.hap --no-submit
storehelper publish --app my-android-app --store apple --file build/Wallet.ipa --no-submit
```

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
`packageId`, or Apple's Build Upload/Build IDs), and prints a safe resume command:

```bash
storehelper resume 20260730T100000Z-a1b2c3d4 --app my-android-app
storehelper runs list
storehelper runs show 20260730T100000Z-a1b2c3d4 --output json
storehelper status --app my-android-app --store harmonyos
storehelper status --app my-android-app --store apple
```

`resume` derives the store from the receipt, verifies the current local artifact digest, starts
from the earliest safe durable step, and does not re-upload a package that was already bound.

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
Temporary upload URLs, signed headers, object IDs, JWTs, and raw vendor responses are never
persisted. See [the security guide](docs/SECURITY_GUIDE.md).

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Success |
| 2 | Invalid usage or configuration |
| 3 | Credential or authentication failure |
| 4 | Package validation failure |
| 5 | Huawei/vendor rejection |
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

For the deliberately opt-in production checklist, see
[`docs/HARMONYOS_MANUAL_TEST.md`](docs/HARMONYOS_MANUAL_TEST.md) or
[`docs/APPLE_MANUAL_TEST.md`](docs/APPLE_MANUAL_TEST.md). Start with read-only credential
verification, then `--dry-run`, then `--no-submit`; only a separately confirmed command
should submit review.

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
