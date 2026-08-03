# Security guide / 安全指南

StoreHelper is local-first: publishing credentials remain on the operator's machine or in the
CI secret store. The project configuration intentionally contains only public application
identifiers and a credential profile name.

StoreHelper 采用本地优先模式：发布凭据只保存在操作者系统钥匙串或 CI 密钥存储中，项目配置文件
只保存公开的应用标识和凭据配置名。

## Credential sources

Credential resolution uses this fixed precedence for the selected store:

1. `STOREHELPER_HUAWEI_CREDENTIALS_FILE`;
2. the complete set of `STOREHELPER_HUAWEI_KEY_ID`,
   `STOREHELPER_HUAWEI_SUB_ACCOUNT`, and `STOREHELPER_HUAWEI_PRIVATE_KEY`;
3. the operating-system keyring profile;
4. a secure prompt, only in an interactive terminal.

Apple uses the equivalent store-specific sources:

1. `STOREHELPER_APPLE_CREDENTIALS_FILE`;
2. `STOREHELPER_APPLE_KEY_TYPE`, `STOREHELPER_APPLE_KEY_ID`,
   `STOREHELPER_APPLE_PRIVATE_KEY`, plus `STOREHELPER_APPLE_ISSUER_ID` for team keys;
3. the Apple keyring namespace selected by `credentials ... --store apple`;
4. the Apple secure prompt in an interactive terminal.

Google Play uses a third, independent namespace and the same precedence rule:

1. `STOREHELPER_GOOGLE_CREDENTIALS_FILE` containing a standard service-account JSON;
2. the complete set of `STOREHELPER_GOOGLE_PROJECT_ID`,
   `STOREHELPER_GOOGLE_PRIVATE_KEY_ID`, `STOREHELPER_GOOGLE_PRIVATE_KEY`, and
   `STOREHELPER_GOOGLE_CLIENT_EMAIL`, plus optional `STOREHELPER_GOOGLE_TOKEN_URI`;
3. the Google keyring namespace selected by `credentials ... --store google_play`;
4. the Google secure prompt in an interactive terminal.

Xiaomi uses a fourth independent namespace:

1. `STOREHELPER_XIAOMI_CREDENTIALS_FILE`;
2. the complete set of `STOREHELPER_XIAOMI_USERNAME`,
   `STOREHELPER_XIAOMI_API_SECRET`, and `STOREHELPER_XIAOMI_PUBLIC_KEY_CERTIFICATE`, plus optional
   `STOREHELPER_XIAOMI_TEST_ACCOUNTS_JSON`;
3. the Xiaomi keyring namespace selected by `credentials ... --store xiaomi`;
4. a secure interactive prompt for the three base fields only.

OPPO uses a fifth, application-specific namespace:

1. `STOREHELPER_OPPO_CREDENTIALS_FILE`;
2. the complete pair `STOREHELPER_OPPO_CLIENT_ID` and `STOREHELPER_OPPO_CLIENT_SECRET`;
3. the OPPO keyring namespace selected by `credentials ... --store oppo`;
4. secure interactive prompts for both values.

Do not commit Service Account JSON, Apple credential JSON, or `.p8` files. StoreHelper refuses
secret-looking fields in `storehelper.yaml`, has no private-key CLI option, and has no plaintext
credential fallback. Team Apple keys require `issuer_id`; individual keys must omit it. Both
require an unencrypted P-256 private key. Google requires a standard `service_account` key with
an unencrypted RSA private key and a valid service-account email. Xiaomi requires the generated
automatic-publishing API secret and Xiaomi's X.509 RSA public certificate. Optional structured
review accounts, passwords/codes, access codes, and audit notes are credentials too; keep them out
of project YAML and source control. OPPO's `client_id` and `client_secret` are both treated as
secrets, and a profile must be scoped to the application for which the OPPO API client was issued.

不要提交 Service Account JSON。StoreHelper 会拒绝 `storehelper.yaml` 中的密钥字段，也不会
通过命令行参数或明文文件保存私钥。

For CI, prefer a secret file created by the CI platform and point
`STOREHELPER_HUAWEI_CREDENTIALS_FILE` to it. Alternatively, configure all three individual
environment variables. Never mix the two forms. Apply the same rule independently to Apple file
and individual environment variables.
Apply the same mutually exclusive file-or-variable rule to Google Play. Use a dedicated service
account with access only to the required Play Console apps and release operations. Rotate and
replace JSON keys according to the organization's key-management policy.
Apply the rule independently to Xiaomi. Prefer a CI secret file for the multiline certificate and
nested review accounts. Resetting the Xiaomi API secret invalidates the old value; replace every
keyring/CI copy together.
Apply the rule independently to OPPO. Never mix the credential file with either individual
variable, and do not reuse one application's OPPO client pair for another package.

## Persisted data

Run receipts contain store names, app IDs, package paths and hashes, logical file names, durable
artifact IDs (`pkgVersion`, `packageId`, Apple Build Upload/Build IDs, Google `versionCode`),
public operation IDs such as a Google App Edit ID, configured release/track/status values, review
submission IDs, release notes, and state timestamps. They never contain private keys,
JWTs, authorization headers, upload `authCode`, signed delivery headers, upload operations,
temporary object IDs, upload URLs, destination URLs, Xiaomi API secrets/certificates, `SIG`,
review accounts, RequestData, OPPO client values/tokens/HMAC signatures/upload signs/file URLs,
listing snapshots, signed forms, or raw vendor bodies. Receipt files are written atomically with
owner-only permissions where the platform supports it.

Xiaomi receipts may contain `submission_started` or `submission_uncertain`. These states mean the
atomic upload-and-review request may have reached Xiaomi and are intentionally non-resumable. The
same app/artifact remains locally blocked until an operator checks Xiaomi's console and explicitly
deletes the local receipt. Deletion never changes Xiaomi state.

OPPO receipts follow the same local blocking rule only after the final mutation begins. A staging
failure happens before `submission_started` and is safe for a new confirmed run. A receipt in
`submission_started` or `submission_uncertain` may represent a completed OPPO review submission;
it is non-resumable and blocks the same app/artifact until an operator checks `status`, reconciles
the OPPO console, and deliberately deletes the local receipt. Deletion never changes OPPO state.

## Network boundary

- TLS verification cannot be disabled.
- Authenticated Huawei redirects are disabled.
- File uploads accept HTTPS URLs only and do not forward Huawei API authorization headers.
- HarmonyOS OBS uploads send only Huawei's exact signed upload headers and disable redirects.
- Apple delivery uploads use HTTPS, send only Apple's exact signed headers for the declared byte
  range, never forward the App Store Connect bearer token, and reject redirects or invalid range
  plans before reading the IPA.
- Google OAuth assertions are sent only to the validated HTTPS `token_uri` from the imported
  credential (normally Google's official OAuth endpoint). Android Publisher bearer tokens are
  sent only to the fixed `androidpublisher.googleapis.com` host; redirects are rejected.
- Google AAB/APK bytes are streamed directly into one App Edit upload request. Media uploads are
  not automatically replayed after an ambiguous response; track updates and commits recover from
  their durable Edit ID and `versionCode`.
- Google commits explicitly use `ERROR_IF_IN_REVIEW` and
  `changesNotSentForReview=false`; StoreHelper never cancels an existing review implicitly.
- Xiaomi requests use only `https://api.developer.xiaomi.com/devupload`; redirects are rejected.
  The read-only query may retry transient failures twice. The `/dev/push` mutation streams one APK
  and icon in one request and is never automatically replayed after any response loss.
- Xiaomi requires MD5 inside its encrypted protocol signature. StoreHelper uses MD5 only for that
  vendor field; durable artifact identity and duplicate detection continue to use SHA-256.
- Xiaomi has no sandbox or automatic review-status API. `--no-submit`, `status`, and automatic
  resume are rejected instead of simulating safety the vendor does not provide.
- OPPO API calls use only `https://oop-openapi-cn.heytapmobi.com`; signed reads may retry bounded
  transient failures, but upload-address allocation, APK upload, and final submission are never
  replayed automatically.
- Dynamic OPPO upload URLs must use HTTPS/default port, have no userinfo or fragment, and match an
  explicit OPPO/HeyTap suffix allowlist. Literal IPs (including private, loopback, link-local, and
  reserved addresses), suffix lookalikes, and redirects are rejected before upload.
- OPPO's vendor-required APK MD5 and opaque file URL exist only in memory. SHA-256 remains the
  durable local identity. `--no-submit` and `resume` are rejected because the upload context is
  intentionally not persisted.
- JWTs and temporary upload values remain inside their store client.
- 401/403 causes one forced JWT renewal; 429/5xx receives a bounded retry.

Apple uploads use the native `buildUploads` and `buildUploadFiles` resources. StoreHelper does not
invoke Xcode, Transporter, or `altool`, and never falls back to another uploader automatically.

## Reporting a vulnerability

Do not open a public issue containing credentials or private application data. Use GitHub's
private vulnerability reporting feature in the StoreHelper repository. Rotate any credential
that may have appeared in a terminal transcript, CI log, issue, or commit history.
