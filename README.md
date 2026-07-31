# StoreHelper

Local-first app store publishing for developers, CI/CD, and AI agents.

StoreHelper 是一个本地优先的应用市场发布 CLI 和 Python SDK。`v0.1.0` 首先提供华为
AppGallery Android Publishing API v2 的 APK/AAB 端到端发布、状态轮询和断点恢复。

> Status: alpha. The first release supports existing Huawei Android applications only.

## Why StoreHelper?

- One command validates, uploads, binds, waits for compilation, updates release notes, and
  submits an Android release.
- Credentials live in the OS keyring or CI secret store—not in project YAML.
- Every durable step has a redacted atomic receipt, so compilation timeouts can be resumed
  without uploading the package again.
- Text output is human-friendly; `--output json` is stable for CI/CD and MCP consumers.
- The Python package is intentionally modular for future Apple and additional Android adapters.

## Requirements and installation

- Python 3.11 or newer
- A Huawei AppGallery Connect application that already exists
- A Huawei Service Account JSON file with `key_id`, `sub_account`, and an unencrypted RSA
  `private_key`

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

Edit `storehelper.yaml`:

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
```

Import credentials into the operating-system keyring and verify read-only app access:

```bash
storehelper credentials import --profile default --file ~/Downloads/huawei-service-account.json
storehelper credentials list
storehelper credentials verify --app my-android-app
```

Validate locally without credentials or network access:

```bash
storehelper publish --app my-android-app --file build/app-release.aab --dry-run
```

Upload and wait for package readiness without changing metadata or submitting review:

```bash
storehelper publish --app my-android-app --file build/app-release.aab --no-submit
```

Publish end to end:

```bash
storehelper publish \
  --app my-android-app \
  --file build/app-release.aab \
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

Huawei compiles packages asynchronously. StoreHelper polls every 15 seconds for up to 10
minutes. A timeout exits with code `6`, preserves the public `pkgVersion`, and prints a safe
resume command:

```bash
storehelper resume 20260730T100000Z-a1b2c3d4 --app my-android-app
storehelper runs list
storehelper runs show 20260730T100000Z-a1b2c3d4 --output json
storehelper status --app my-android-app
```

`resume` starts from the earliest safe durable step and does not re-upload a package that was
already bound.

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

Then run the same `publish --yes --output json` command. Do not store credentials in
`storehelper.yaml` or pass a private key on the command line. See
[the security guide](docs/SECURITY_GUIDE.md).

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

## Huawei troubleshooting

- `204144662`: the package could not be bound. StoreHelper uses one sanitized logical filename
  (maximum 64 characters) for both upload and binding to avoid the common `fileName` mismatch.
- `204144727`: Huawei is still compiling. StoreHelper polls before submission and treats this as
  resumable eventual consistency.
- `204144735`: Huawei is still performing security detection; resume after the timeout.
- `401/403` or `204144665`: verify the Service Account, app access, system time, and configured
  `app_id`/package name.

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
