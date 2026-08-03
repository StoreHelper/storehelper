# OPPO live verification / OPPO 软件商店线上验证

This checklist is deliberately opt-in. Automated tests use generated APKs, fake identifiers, and
mocked HTTP only. They never contact OPPO, allocate a live upload URL, upload a real APK, or call
the final update endpoint.

本文档中的线上步骤必须由发布负责人主动执行。自动化测试不会连接 OPPO，也不会申请真实上传
地址、上传安装包或提交审核。首次真实调用必须遵循团队的生产变更与审批流程。

Official entry points:

- [OPPO Open Platform documentation](https://open.oppomobile.com/new/wiki)
- [OPPO documentation center](https://open.oppomobile.com/documentation/page/center)

The detailed protocol pages may require an authenticated OPPO developer session. The official
console and documentation are authoritative for API-client enablement and current live limits.

## 1. Confirm the supported scope / 确认支持范围

The v0.6.0 adapter intentionally supports only:

- an existing mainland-China OPPO Software Store application owned by the API client;
- one signed APK, no larger than 2 GiB;
- a configured positive `version_code` greater than the current OPPO version;
- one full online update (`online_type=1`);
- required release notes containing 1–500 characters;
- reuse of the existing name, categories, descriptions, privacy URL, icon, screenshots,
  age/copyright values, and business contacts returned by OPPO.

It does not create or claim apps, publish games, upload AAB/multiple APKs, change listing metadata,
schedule release, or keep an upload-only draft. If required listing data is missing, fix it in the
OPPO console; StoreHelper fails closed instead of supplying generic text.

当前版本只支持已有应用的单 APK 全量更新。它不会创建/认领应用、发布游戏、上传 AAB/多 APK、
修改商店详情或定时发布。OPPO 返回的必填详情不完整时，应先在控制台补齐。

## 2. Create application-specific API access / 创建应用专属 API 客户端

- Sign in to the OPPO developer console with an account that owns the existing package.
- Open the application's API/Open Platform settings and create or enable its publishing API
  client according to the current official console instructions.
- Record the issued `client_id` and `client_secret` in the approved secret manager. Treat both as
  secrets and do not reuse the pair for a different package.
- Confirm the application is not frozen, offline, or already in a conflicting review state.
- Prepare a signed APK with the exact existing package name and the configured new version code.

请为目标应用创建独立的 API 客户端，并将 `client_id` 与 `client_secret` 都按密钥管理。不要在
多个 OPPO 应用之间复用配置名或客户端凭据。

## 3. Configure public values only / 只配置公开字段

All values below are fake:

```yaml
version: 1
apps:
  manual-oppo-test:
    package_name: com.example.wallet
    stores:
      oppo:
        credential_profile: manual-oppo
        version_code: 123
        language: zh-CN
```

```bash
storehelper config validate --config storehelper.yaml
```

Do not put either client value, an access token, signature, upload URL/sign, or copied application
detail in YAML. `version_code` must be an integer and must match the signed APK.

## 4. Import credentials securely / 安全导入凭据

Create a temporary JSON file outside the repository:

```json
{
  "client_id": "replace-with-oppo-client-id",
  "client_secret": "replace-with-oppo-client-secret"
}
```

```bash
storehelper credentials import \
  --store oppo \
  --profile manual-oppo \
  --file ~/Downloads/oppo-api.json

storehelper credentials list --store oppo
```

After keyring import, remove the plaintext file according to company policy. For CI, prefer
`STOREHELPER_OPPO_CREDENTIALS_FILE`. The alternative complete pair is
`STOREHELPER_OPPO_CLIENT_ID` plus `STOREHELPER_OPPO_CLIENT_SECRET`; never mix file and individual
forms and never place either value in command history.

## 5. Run zero-network validation / 执行零网络校验

```bash
storehelper publish \
  --app manual-oppo-test \
  --store oppo \
  --file build/app-release.apk \
  --release-notes "Manual verification build" \
  --dry-run \
  --output json \
  --config storehelper.yaml
```

Confirm exit code `0`, `"store":"oppo"`, and `"stage":"completed"`. This validates configuration,
APK/ZIP structure, APK-only scope, the 2 GiB boundary, logical filename, SHA-256, and vendor-required
MD5 without reading credentials or performing network I/O.

`--no-submit` is intentionally rejected: the temporary upload URL/file reference is kept only in
memory and is useful only for the immediately following final request.

## 6. Verify credentials and application read-only / 只读验证凭据与应用

```bash
storehelper credentials verify \
  --app manual-oppo-test \
  --store oppo \
  --output json \
  --config storehelper.yaml
```

This obtains a token and calls only signed application detail. It requires an exact package match,
a numeric current version lower than the configured version, a publishable review state, and all
listing fields needed by the update endpoint. Stop on any authentication, package, version,
review-state, or incomplete-listing error. Do not proceed by guessing missing values.

## 7. Obtain separate approval for the live mutation / 单独审批真实提交

Before proceeding, record and review:

- APK SHA-256, package name, signing identity, and new version code;
- current OPPO console version and review state;
- the existing listing values that will be reused and the 1–500 character update note;
- the absence of upload-only mode, automatic resume, and a safe automatic retry after final send;
- the release owner who approved this exact submission.

Then run exactly one separately confirmed command:

```bash
storehelper publish \
  --app manual-oppo-test \
  --store oppo \
  --file build/app-release.apk \
  --release-notes-file RELEASE_NOTES.md \
  --yes --output json \
  --config storehelper.yaml
```

StoreHelper repeats the application query, allocates one allowlisted HTTPS upload URL, streams one
APK, persists `submission_started`, and sends one final `/resource/v1/app/upd` form. Success returns
`"stage":"submitted"`. Confirm the new version directly in the OPPO console.

## 8. Query status and reconcile uncertainty / 查询状态并处理不确定结果

```bash
storehelper status \
  --app manual-oppo-test \
  --store oppo \
  --output json \
  --config storehelper.yaml
```

If staging fails before `submission_started`, a new confirmed run is safe. If interruption, process
loss, timeout, or response loss occurs after final submission begins, the receipt remains
`submission_started` or `submission_uncertain`; StoreHelper blocks the same app/artifact and does
not offer `resume`:

```bash
storehelper runs list
storehelper runs show RUN_ID --output json
```

Use both `status` and the OPPO console to determine whether the version arrived. Only when a release
owner deliberately approves another attempt should the local block be acknowledged:

```bash
storehelper runs delete RUN_ID --yes
```

Then run a new confirmed publish. Deleting the receipt changes only local state; it never withdraws,
cancels, or modifies an OPPO submission.

## 9. Incident handling / 事件处理

- If a client value, access token, HMAC signature, upload sign/URL, signed form, listing snapshot,
  or raw response appears in a transcript, CI log, issue, or artifact, stop the release and follow
  the organization's incident process. Rotate the OPPO client secret when applicable.
- StoreHelper receipts must contain none of those values. Preserve only the approved public release
  evidence and local APK SHA-256.
- Treat `OPPO_UPLOAD_HOST_UNSAFE` as a security stop. Do not bypass HTTPS, redirect, port, IP, or
  suffix validation; verify the endpoint against current official OPPO documentation/support.
