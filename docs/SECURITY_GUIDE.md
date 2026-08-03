# Security guide / 安全指南

StoreHelper is local-first: publishing credentials remain on the operator's machine or in the
CI secret store. The project configuration intentionally contains only public application
identifiers and a credential profile name.

StoreHelper 采用本地优先模式：发布凭据只保存在操作者系统钥匙串或 CI 密钥存储中，项目配置文件
只保存公开的应用标识和凭据配置名。

## Credential sources

Credential resolution uses this fixed precedence:

1. `STOREHELPER_HUAWEI_CREDENTIALS_FILE`;
2. the complete set of `STOREHELPER_HUAWEI_KEY_ID`,
   `STOREHELPER_HUAWEI_SUB_ACCOUNT`, and `STOREHELPER_HUAWEI_PRIVATE_KEY`;
3. the operating-system keyring profile;
4. a secure prompt, only in an interactive terminal.

Do not commit Service Account JSON. StoreHelper refuses secret-looking fields in
`storehelper.yaml`, has no private-key CLI option, and has no plaintext credential fallback.

不要提交 Service Account JSON。StoreHelper 会拒绝 `storehelper.yaml` 中的密钥字段，也不会
通过命令行参数或明文文件保存私钥。

For CI, prefer a secret file created by the CI platform and point
`STOREHELPER_HUAWEI_CREDENTIALS_FILE` to it. Alternatively, configure all three individual
environment variables. Never mix the two forms.

## Persisted data

Run receipts contain store names, app IDs, package paths and hashes, logical file names, durable
artifact IDs (`pkgVersion` or `packageId`), release notes, and state timestamps. They never
contain private keys, JWTs, authorization headers, upload `authCode`, OBS signed headers,
temporary object IDs, upload URLs, destination URLs, or raw Huawei bodies. Receipt files are
written atomically with owner-only permissions where the platform supports it.

## Network boundary

- TLS verification cannot be disabled.
- Authenticated Huawei redirects are disabled.
- File uploads accept HTTPS URLs only and do not forward Huawei API authorization headers.
- HarmonyOS OBS uploads send only Huawei's exact signed upload headers and disable redirects.
- JWTs and temporary upload values remain inside the Huawei client.
- 401/403 causes one forced JWT renewal; 429/5xx receives a bounded retry.

## Reporting a vulnerability

Do not open a public issue containing credentials or private application data. Use GitHub's
private vulnerability reporting feature in the StoreHelper repository. Rotate any credential
that may have appeared in a terminal transcript, CI log, issue, or commit history.
