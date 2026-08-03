# Xiaomi live verification / 小米应用商店线上验证

This checklist is deliberately opt-in. Automated tests use generated certificates, fake
identifiers, and mocked HTTP only. They never contact Xiaomi or call the live-only `/dev/push`
mutation.

本文档中的线上步骤必须由发布负责人主动执行。自动化测试不会连接小米，也不会上传或提交真实
应用。小米目前没有自动发布沙箱，首次真实提交必须按生产变更管理。

Authoritative references:

- [Xiaomi automatic publishing API](https://dev.mi.com/xiaomihyperos/documentation/detail?pId=1134)
- [Xiaomi manual app submission](https://dev.mi.com/docs/appsmarket/distribution/app_submit/)

## 1. Confirm supported scope

The v0.5.0 adapter intentionally supports only:

- a package that already exists under the authenticated Xiaomi developer account;
- `synchroType=1` existing-app version updates;
- one signed APK, no larger than 2 GiB;
- phone target (`suitableType=0`);
- one required PNG icon, HTTPS privacy URL, app name, and 1–500 character update note;
- optional structured reviewer accounts in the format required since 2026-02-04.

It does not create packages/apps, upload AAB, dual APKs, channel APKs, screenshots, tablet
listings, scheduled releases, metadata-only updates, or international-store releases.

## 2. Prepare Xiaomi developer access

- Sign in to the Xiaomi developer console with the email that owns the existing package.
- Open the automatic-publishing/API settings and obtain the generated API private value and the
  Xiaomi X.509 public certificate. Use the generated API value, not the interactive login
  password. Resetting it invalidates every old copy.
- Confirm the package is visible under this account and is currently eligible for a version
  update. Resolve an active review or ownership/claim issue in the console first.
- Prepare a signed APK with the exact existing package name and a new acceptable version code.
- Prepare the production PNG icon and public HTTPS privacy-policy URL.

Xiaomi's API has no sandbox. A successful push immediately becomes a review submission.

## 3. Configure only public values

All values below are fake. The icon path is resolved relative to `storehelper.yaml`:

```yaml
version: 1
apps:
  manual-xiaomi-test:
    package_name: com.example.wallet
    stores:
      xiaomi:
        credential_profile: manual-xiaomi
        app_name: Example Wallet
        icon: assets/xiaomi-icon.png
        privacy_url: https://example.com/privacy
        language: zh-CN
```

```bash
storehelper config validate --config storehelper.yaml
```

Do not put the developer email, API secret, certificate, reviewer login, password/code, access
code, or audit note in YAML.

## 4. Import credentials securely

Create a temporary JSON file outside the repository:

```json
{
  "username": "developer@example.com",
  "api_secret": "replace-with-the-generated-api-secret",
  "public_key_certificate": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n",
  "test_accounts": {
    "zh_CN": {
      "accounts": [
        {
          "login_type": 1,
          "account": "reviewer@example.com",
          "password": "replace-with-review-password",
          "access_code": "replace-with-access-code"
        }
      ],
      "audit_notes": "Open the demo workspace and follow the supplied test flow."
    }
  }
}
```

`login_type` is `1` for account/password or `2` for phone/verification code. `account` and
`password` must be supplied together and each `account`, `password`, or `access_code` is at most
50 characters. Each locale accepts at most five accounts; `audit_notes` is at most 500
characters. Locale keys use `language_COUNTRY`, for example `zh_CN`. Omit `test_accounts` when the
app needs no reviewer access; do not use the obsolete free-text format.

```bash
storehelper credentials import \
  --store xiaomi \
  --profile manual-xiaomi \
  --file ~/Downloads/xiaomi-api.json

storehelper credentials list --store xiaomi
```

After keyring import, remove the temporary plaintext file according to company policy. For CI,
prefer `STOREHELPER_XIAOMI_CREDENTIALS_FILE`. The alternative individual variables are
`STOREHELPER_XIAOMI_USERNAME`, `STOREHELPER_XIAOMI_API_SECRET`,
`STOREHELPER_XIAOMI_PUBLIC_KEY_CERTIFICATE`, and optional
`STOREHELPER_XIAOMI_TEST_ACCOUNTS_JSON`. Never mix file and individual forms.

## 5. Run zero-network validation

```bash
storehelper publish \
  --app manual-xiaomi-test \
  --store xiaomi \
  --file build/app-release.apk \
  --release-notes "Manual verification build" \
  --dry-run \
  --output json \
  --config storehelper.yaml
```

Confirm exit code `0`, `"store":"xiaomi"`, and `"stage":"completed"`. This checks the
configuration, HTTPS privacy URL, relative readable PNG, APK archive, APK-only rule, 2 GiB limit,
logical filename, and SHA-256 without reading credentials or performing network I/O.

`--no-submit` is intentionally rejected because Xiaomi cannot upload without submitting review.

## 6. Verify package access with the read-only API

```bash
storehelper credentials verify \
  --app manual-xiaomi-test \
  --store xiaomi \
  --output json \
  --config storehelper.yaml
```

This calls only signed `/dev/query`. It requires an exact returned package-name match and
`updateVersion=true`. Stop on package claim, package absence, credential/signature failure, or
update denial. `status --store xiaomi` is unsupported; Xiaomi does not expose review/on-shelf
status through this API.

## 7. Obtain approval before the one live mutation

Before proceeding, record and review:

- APK SHA-256 and signing/version identity;
- existing Xiaomi package and console review state;
- app name, PNG icon, privacy URL, update note, and reviewer-account values;
- the fact that there is no sandbox, upload-only mode, automatic status, or safe automatic retry;
- the release owner who approved the submission.

Then run exactly one separately confirmed command:

```bash
storehelper publish \
  --app manual-xiaomi-test \
  --store xiaomi \
  --file build/app-release.apk \
  --release-notes-file RELEASE_NOTES.md \
  --yes --output json \
  --config storehelper.yaml
```

StoreHelper first repeats read-only verification, persists `submission_started`, and then streams
one multipart `/dev/push` request. Success returns `"stage":"submitted"`. Verify the new review
submission directly in Xiaomi's console.

## 8. Handle uncertain results without replay

If the process is interrupted or the network response is lost after submission begins, the local
receipt is `submission_started` or `submission_uncertain`. StoreHelper does not retry and does not
offer `resume`:

```bash
storehelper runs list
storehelper runs show RUN_ID --output json
```

The same app/artifact remains blocked. Inspect Xiaomi's developer console and determine whether
the submission arrived. If it did, keep normal review handling and delete the local receipt only
when it is no longer needed. If it did not and a release owner approves another attempt, explicitly
acknowledge the decision:

```bash
storehelper runs delete RUN_ID --yes
```

Then run a new confirmed publish. Deleting a receipt changes only local state; it never withdraws,
cancels, or modifies a Xiaomi submission.

## 9. Incident handling

- If the API secret, certificate file, reviewer credentials, SIG, or request body appears in a
  terminal transcript, CI log, issue, or artifact, stop the release and follow the organization's
  incident process. Reset the Xiaomi API secret and replace stored copies when applicable.
- StoreHelper receipts must not contain the API secret, certificate, SIG, reviewer values,
  RequestData, multipart body, or raw response. Preserve only the approved public release evidence.
- Handle `XIAOMI_PACKAGE_CLAIM_REQUIRED`, `XIAOMI_UPDATE_NOT_ALLOWED`, and deterministic APK/test
  account rejection in the developer console before starting a new confirmed run.
