# vivo live verification / vivo 软件商店线上验证

This checklist is deliberately opt-in. Automated tests use generated APKs, fake identifiers, and
mocked HTTP only. They never contact vivo, upload a real APK, or call the final update endpoint.

本文档中的线上步骤必须由发布负责人主动执行。自动化测试不会连接 vivo、上传真实 APK 或提交
审核。首次真实调用必须遵循团队的生产变更与审批流程。

Official entry points:

- [vivo application API documentation](https://dev.vivo.com.cn/documentCenter/doc/326)
- [vivo application update API documentation](https://dev.vivo.com.cn/documentCenter/doc/327)

The page bodies may require a browser session with JavaScript. The current official vivo console
and documentation are authoritative for API enablement, account permissions, and live limits.

## 1. Confirm the supported scope / 确认支持范围

The v0.7.0 adapter intentionally supports only:

- an existing mainland-China vivo phone application owned by the API account;
- one signed APK, no larger than 3 GiB;
- a positive configured `version_code` greater than the current vivo version;
- release notes containing 5–200 characters after trimming;
- immediate publication after approval (`onlineType=1`, `compatibleDevice=1`);
- read-only status values `1`–`6`, with unknown values reported as `unknown`.

It does not create apps, upload AAB/multiple APKs, change listing metadata, schedule publication,
configure phased rollout, or upload privacy self-check documents. The APK package and version must
be reviewed independently before the one live mutation.

当前版本只支持已有 vivo 手机应用的单 APK 更新，不会创建应用、修改商店详情、定时发布或配置
灰度策略。提交前请由发布负责人核对 APK 包名、签名和 versionCode。

## 2. Enable publishing API access / 开通发布 API 权限

- Sign in to the vivo developer console with the account that owns the package.
- Enable the application publishing/update API according to the current official instructions.
- Record the issued `access_key` and `secret_key` in the approved secret manager. Treat both as
  secrets; the name `access_key` does not make it public.
- Confirm that the package is not suspended and has no conflicting update under review.
- Prepare a signed APK with the existing package and intended new version code.

## 3. Configure public values only / 只配置公开字段

All values below are fake:

```yaml
version: 1
apps:
  manual-vivo-test:
    package_name: com.example.wallet
    stores:
      vivo:
        credential_profile: manual-vivo
        version_code: 124
        language: zh-CN
```

```bash
storehelper config validate --config storehelper.yaml
```

Do not put either key, an HMAC signature, APK MD5, upload serial number, request form, or copied
vendor response in YAML. The locale is retained for consistent public configuration but is not
sent by the v0.7.0 vivo protocol subset.

## 4. Import credentials securely / 安全导入凭据

Create a temporary JSON file outside the repository:

```json
{
  "access_key": "replace-with-vivo-access-key",
  "secret_key": "replace-with-vivo-secret-key"
}
```

```bash
storehelper credentials import \
  --store vivo \
  --profile manual-vivo \
  --file ~/Downloads/vivo-api.json

storehelper credentials list --store vivo
```

After keyring import, remove the plaintext file according to company policy. For CI, prefer
`STOREHELPER_VIVO_CREDENTIALS_FILE`. The alternative complete pair is
`STOREHELPER_VIVO_ACCESS_KEY` plus `STOREHELPER_VIVO_SECRET_KEY`; never mix file and individual
forms and never put either value in command history.

## 5. Run zero-network validation / 执行零网络校验

```bash
storehelper publish \
  --app manual-vivo-test \
  --store vivo \
  --file build/app-release.apk \
  --release-notes "Manual verification build" \
  --dry-run \
  --output json \
  --config storehelper.yaml
```

Confirm exit code `0`, `"store":"vivo"`, and `"stage":"completed"`. This validates the strict
configuration, APK/ZIP structure, APK-only scope, 3 GiB boundary, logical filename, SHA-256, and
vendor-required streamed MD5 without reading credentials or performing network I/O.

`--no-submit` is intentionally rejected: the temporary upload serial number and MD5 are kept only
in memory for the immediately following final request.

## 6. Verify credentials and application read-only / 只读验证凭据与应用

```bash
storehelper credentials verify \
  --app manual-vivo-test \
  --store vivo \
  --output json \
  --config storehelper.yaml
```

This calls only signed `app.query.details` at the fixed vivo gateway. It requires an exact package
match, a positive current version, a configured higher version, and a publishable known state.
Stop on authentication, package, version, review-state, or unknown-status errors. Do not proceed by
guessing what the console contains.

## 7. Obtain separate approval for the live mutation / 单独审批真实提交

Before proceeding, record and review:

- APK SHA-256, package name, signing identity, and new version code;
- current vivo console version and review state;
- the 5–200 character update note and immediate-online/phone-only behavior;
- the absence of upload-only mode, automatic resume, and safe mutation retry;
- the release owner who approved this exact APK and submission.

Then run exactly one separately confirmed command:

```bash
storehelper publish \
  --app manual-vivo-test \
  --store vivo \
  --file build/app-release.apk \
  --release-notes-file RELEASE_NOTES.md \
  --yes --output json \
  --config storehelper.yaml
```

StoreHelper re-queries the application, streams one APK to `app.upload.apk.app.64`, persists
`submission_started`, and sends one final `app.sync.update.app` request. Success returns
`"stage":"submitted"`. Confirm the new version directly in the vivo console.

## 8. Query status and reconcile uncertainty / 查询状态并处理不确定结果

```bash
storehelper status \
  --app manual-vivo-test \
  --store vivo \
  --output json \
  --config storehelper.yaml
```

An upload failure before `submission_started` is safe for a new confirmed run. Interruption,
process loss, timeout, or response loss after final submission begins leaves the receipt at
`submission_started` or `submission_uncertain`; StoreHelper blocks the same app/artifact and does
not offer `resume`:

```bash
storehelper runs list
storehelper runs show RUN_ID --output json
```

Use both `status` and the vivo console to determine whether the version arrived. Only when a
release owner deliberately approves another attempt should the local block be acknowledged:

```bash
storehelper runs delete RUN_ID --yes
```

Then run a new confirmed publish. Deleting the receipt changes only local state; it never cancels
or modifies a vivo submission.

## 9. Incident handling / 事件处理

- If either key, an HMAC signature, APK MD5, upload serial number, signed form, or raw response
  appears in a transcript, CI log, issue, or artifact, stop the release and follow the
  organization's incident process. Rotate the credential pair when applicable.
- StoreHelper receipts must contain none of those values. Preserve only approved public release
  evidence and the local APK SHA-256.
- Do not bypass TLS verification, fixed-host routing, redirect rejection, response-size limits, or
  zero-retry mutation policy. Verify protocol changes against current official vivo documentation.
