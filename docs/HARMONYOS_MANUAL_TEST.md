# HarmonyOS live verification / HarmonyOS 线上验证

This checklist is deliberately opt-in. Automated tests use mocked HTTP only; they never upload a
real package or submit an application for review.

本文档中的线上步骤必须由操作者主动执行。自动化测试只使用模拟 HTTP，不会上传真实安装包，
也不会提交审核。

## 1. Preconditions

- Use a HarmonyOS application that already exists in AppGallery Connect.
- Confirm the Service Account can access that application.
- Prepare a signed `.app` or `.hap` whose package name matches the configured application.
- Use non-production release notes for the first verification where company policy permits it.
- Never paste the Service Account JSON, private key, JWT, signed OBS URL, or signed headers into
  an issue, terminal transcript, or project configuration.

## 2. Configure and verify read-only access

Use your own values locally; the values below are intentionally fake:

```yaml
version: 1
apps:
  manual-harmony-test:
    package_name: com.example.android
    stores:
      harmonyos:
        app_id: "987654321"
        package_name: com.example.harmony
        credential_profile: manual-test
        language: zh-CN
```

```bash
storehelper config validate --config storehelper.yaml
storehelper credentials import \
  --profile manual-test \
  --file ~/Downloads/huawei-service-account.json
storehelper credentials verify \
  --app manual-harmony-test \
  --store harmonyos \
  --config storehelper.yaml
```

The verification command is read-only. Stop if it reports an app-ID, package-name, credential,
or authorization mismatch.

## 3. Local-only dry run

This command validates configuration, archive integrity, type, size, logical filename, and digest.
It performs zero network calls and needs no credentials:

```bash
storehelper publish \
  --app manual-harmony-test \
  --store harmonyos \
  --file build/release/wallet.app \
  --dry-run \
  --config storehelper.yaml
```

Repeat with a `.hap` if that is a supported artifact in your release process.

## 4. Upload and bind without review submission

This step changes AppGallery Connect by uploading and binding a package, but it does not update
release notes or submit review:

```bash
storehelper publish \
  --app manual-harmony-test \
  --store harmonyos \
  --file build/release/wallet.app \
  --no-submit \
  --config storehelper.yaml
```

Confirm the result reaches `package_ready`. Inspect the redacted local receipt and compare the
draft package in AppGallery Connect:

```bash
storehelper runs list
storehelper runs show RUN_ID --output json
```

The receipt may contain the durable `packageId`. It must not contain an OBS URL, object ID,
authorization header, JWT, or private key.

## 5. Optional review submission

Run this only after the uploaded package and release notes have been reviewed. It updates the
configured language's `newFeatures` field and submits a formal full release. The CLI asks for
confirmation in an interactive terminal:

```bash
storehelper publish \
  --app manual-harmony-test \
  --store harmonyos \
  --file build/release/wallet.app \
  --release-notes "Manual StoreHelper verification" \
  --config storehelper.yaml
```

CI is non-interactive and therefore also requires `--yes`. Do not add `--yes` to exploratory
commands or scripts until review submission is intended.

```bash
storehelper status \
  --app manual-harmony-test \
  --store harmonyos \
  --config storehelper.yaml
```

## 6. Timeout and recovery

If processing exceeds the configured wait window, StoreHelper exits with code `6` and prints a
resume command. Keep the original local artifact unchanged:

```bash
storehelper resume RUN_ID \
  --app manual-harmony-test \
  --poll-interval 15s \
  --wait-timeout 10m \
  --config storehelper.yaml
```

Resume derives `harmonyos` from the receipt, verifies the local SHA-256 digest, and continues from
the durable `packageId`; it does not upload the package again. Huawei error `204144727` is handled
as eventual consistency: StoreHelper polls again and retries submission once the package is ready.

## 7. Cleanup and incident handling

- Remove unwanted draft releases in AppGallery Connect according to your organization's policy.
- Delete only the local receipt with `storehelper runs delete RUN_ID` when recovery is no longer
  required; this command does not change vendor state.
- If a credential or signed upload value was exposed, stop, rotate the Service Account credential,
  and follow your organization's incident process.
- StoreHelper does not automate rollback, release withdrawal, staged rollout, or production-track
  selection in v0.2.0.
