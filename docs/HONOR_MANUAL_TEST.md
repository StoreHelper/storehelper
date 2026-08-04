# HONOR live verification / 荣耀应用市场线上验证

This checklist is deliberately opt-in. Automated tests use generated APKs, fake identifiers, and
mocked HTTP transports only. They never contact HONOR, upload a real APK, or submit a review.

本文档中的线上步骤必须由发布负责人主动执行。自动化测试不会连接荣耀、上传真实 APK 或提交
审核。首次真实调用必须遵循团队的生产变更与审批流程。

Official references / 官方资料：

- [HONOR Publish API guide](https://developer.honor.com/cn/doc/guides/101359)
- [HONOR APK release guide](https://developer.honor.com/cn/doc/guides/100884)
- [HONOR review guideline](https://developer.honor.com/cn/doc/guides/100879)

The current official console and documentation remain authoritative for API enablement, account
permissions, field limits, review policy, and production behavior.

## 1. Confirm the supported scope / 确认支持范围

The v0.8.0 adapter intentionally supports only:

- an existing mainland-China HONOR phone application owned by the API account;
- one signed APK strictly smaller than 4 GiB;
- a positive configured `version_code` greater than the published version;
- one exact existing locale and release notes containing 1–500 characters;
- non-forced, immediate full publication after approval (`forceUpdate=0`, `releaseType=1`);
- current-release status values `0`–`4`, with anything else reported as `unknown`.

It does not create apps, upload AAB/multiple APKs, change listing fields other than the selected
locale's `newFeature`, delete other locales, schedule publication, configure phased release, or
send reviewer accounts. Check the APK package, signing identity, and version independently.

当前版本只支持已有荣耀手机应用的单 APK 更新，不会创建应用、修改其他商店资料、定时发布或
配置分阶段发布。提交前请由发布负责人核对包名、签名和 versionCode。

## 2. Enable API access / 开通 API 权限

- Sign in to the HONOR developer console with the account that owns the exact package.
- Under the current API/open-capability credentials page, create or obtain the account-level
  `client_id` and API secret. Treat both as secrets.
- Confirm the account can query the package and that no release is reviewing or left in an
  unrelated editing draft.
- Prepare a signed APK with a version code above the published version.

Do not copy app `secretKey`, test accounts, passwords, audit attachments, full detail responses,
or temporary upload URLs into project files, issues, or logs.

## 3. Configure public values only / 只配置公开字段

All values below are fake:

```yaml
version: 1
apps:
  manual-honor-test:
    package_name: com.example.wallet
    stores:
      honor:
        credential_profile: manual-honor
        version_code: 125
        language: zh-CN
```

```bash
storehelper config validate --config storehelper.yaml
```

Only the profile name, version code, package, and locale belong in YAML. Never add the API client
pair, bearer token, upload URL, `objectId`, app snapshot, or audit response.

## 4. Import credentials securely / 安全导入凭据

Create a temporary JSON file outside the repository:

```json
{
  "client_id": "replace-with-honor-client-id",
  "client_secret": "replace-with-honor-client-secret"
}
```

```bash
storehelper credentials import \
  --store honor \
  --profile manual-honor \
  --file ~/Downloads/honor-api.json

storehelper credentials list --store honor
```

After keyring import, remove the plaintext file according to company policy. For CI, prefer
`STOREHELPER_HONOR_CREDENTIALS_FILE`. The alternative complete pair is
`STOREHELPER_HONOR_CLIENT_ID` plus `STOREHELPER_HONOR_CLIENT_SECRET`; never mix file and individual
forms and never put either value in command history.

## 5. Run zero-network validation / 执行零网络校验

```bash
storehelper publish \
  --app manual-honor-test \
  --store honor \
  --file build/app-release.apk \
  --release-notes "Manual verification build" \
  --dry-run \
  --output json \
  --config storehelper.yaml
```

Confirm exit code `0`, `"store":"honor"`, and `"stage":"completed"`. This validates strict
configuration, APK-only/ZIP/signature structure, the strict 4 GiB boundary, safe logical name, and
SHA-256 without reading credentials or performing network I/O.

`--no-submit` is intentionally rejected. HONOR upload objects remain temporary until bound, so an
upload-only result is not treated as a safe durable draft.

## 6. Verify credentials and application read-only / 只读验证凭据与应用

```bash
storehelper credentials verify \
  --app manual-honor-test \
  --store honor \
  --output json \
  --config storehelper.yaml
```

This obtains a token at the fixed HONOR identity host, then uses only package-to-APPID, app-detail,
and current-release reads at the fixed Publish API host. It requires an exact package, the selected
existing locale, a higher target version, and no review/draft conflict. Stop on any identity,
version, locale, or unknown-state error; reconcile the console rather than guessing.

## 7. Obtain separate approval for live mutation / 单独审批真实提交

Before proceeding, record and review:

- APK SHA-256, package name, signing identity, and intended version code;
- current published version, release state, and configured `language`;
- the exact 1–500 character `newFeature` text;
- immediate full publication and non-forced update behavior;
- the release owner who approved this exact artifact and submission.

Then run exactly one separately confirmed command:

```bash
storehelper publish \
  --app manual-honor-test \
  --store honor \
  --file build/app-release.apk \
  --release-notes-file RELEASE_NOTES.md \
  --yes --output json \
  --config storehelper.yaml
```

StoreHelper allocates `fileType=100`, ignores the returned dynamic upload URL, streams the APK to
HONOR's fixed upload endpoint, binds only that object, preserves the selected locale's app name and
descriptions, changes only `newFeature` with `setAll=0`, and submits exactly
`{"forceUpdate":0,"releaseType":1}`. Success returns `"stage":"submitted"`. Confirm the release in
the HONOR console.

## 8. Query status and resume safely / 查询状态并安全恢复

```bash
storehelper status \
  --app manual-honor-test \
  --store honor \
  --output json \
  --config storehelper.yaml
```

If publish exits with code `6`, retain the receipt and resume it:

```bash
storehelper runs show RUN_ID --output json
storehelper resume RUN_ID --output json --config storehelper.yaml
```

The receipt may contain only public recovery values: `APPID`, `objectId:sha256`, release ID, target
configuration, artifact path/hash, release notes, and timestamps. Resume reads current bindings,
localized notes, and release state before deciding whether a write is still required. It never
automatically sends a second audit submission after an ambiguous response.

Do not manually edit a receipt or transplant it to another package. `HONOR_RECEIPT_MISMATCH`
protects against using one application's durable context for another application.

## 9. Incident handling / 事件处理

- If either client value, a bearer token, app `secretKey`, test credentials, upload URL, full app
  snapshot, audit attachment, or raw response appears in a transcript, CI log, issue, or artifact,
  stop the release and follow the organization's incident process. Rotate credentials when needed.
- Receipts may retain the numeric `objectId` only as `objectId:sha256`; this is the documented public
  recovery link, not an authentication secret.
- Do not bypass TLS verification, fixed-host routing, redirect rejection, response-size limits,
  zero-retry mutations, or read-before-write reconciliation. Verify protocol changes against the
  current official HONOR documentation before changing the adapter.
