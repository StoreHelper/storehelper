# Xiaomi App Store Publishing Design

**Status:** Approved under the maintainer's standing authorization to use the safest recommended
design
**Date:** 2026-08-03
**Target release:** v0.5.0

## 1. Context and authoritative protocol

StoreHelper v0.4.0 supports Huawei Android, HarmonyOS, Apple, and Google Play with local-first
credentials and durable publishing receipts. Xiaomi is the next Android adapter.

The design is based only on Xiaomi's current first-party material:

- [Application automatic publishing API](https://dev.mi.com/xiaomihyperos/documentation/detail?pId=1134)
- [Manual application submission flow](https://dev.mi.com/docs/appsmarket/distribution/app_submit/)
- the official Python 3 sample linked from the automatic publishing guide

The guide was updated on 2026-02-02 and says:

- the China store endpoint is `https://api.developer.xiaomi.com/devupload`;
- `/dev/query` reads the latest package information and update permissions;
- `/dev/push` uploads files and submits the application in one multipart request;
- APK is supported; the documented flow does not accept AAB;
- update mode is `synchroType=1` and requires `updateDesc`;
- `icon` is a required multipart file;
- signatures contain MD5 digests required by the vendor protocol, the developer API secret, and
  are encrypted in RSA PKCS#1 v1.5 chunks with Xiaomi's X.509 public certificate;
- upload size is at most 2 GiB;
- there is no sandbox and no API for review/on-shelf status;
- structured `testAccount` data supersedes the legacy string format from 2026-02-04.

The old downloadable sample still declares an HTTP base URL. StoreHelper follows the newer guide
and accepts only the documented HTTPS host.

## 2. Goals

- Add `--store xiaomi` for a package that already exists under the authenticated Xiaomi account.
- Support one signed `.apk` update with existing application metadata.
- Verify account/package ownership and update readiness through `/dev/query` before mutation.
- Store the developer email, generated API secret, Xiaomi public certificate, and optional
  structured review accounts in an independent keyring namespace.
- Validate the APK and required PNG icon locally, including the 2 GiB vendor limit.
- Build exact UTF-8 JSON strings, stream MD5 digests, encrypt `SIG` according to Xiaomi's official
  sample, and stream the multipart upload without buffering the APK.
- Map release notes to `appInfo.updateDesc` and send existing app name/privacy URL from public
  configuration.
- Treat successful `/dev/push` as accepted for Xiaomi review.
- Prevent automatic retries after an ambiguous mutation and persist an explicit uncertain state.
- Keep all tests offline; never call the live-only Xiaomi mutation from automation.

## 3. Deliberate scope

The first Xiaomi adapter supports existing-app APK updates only:

```text
synchroType=1
one APK
phone target (suitableType=0)
required appName, packageName, privacyUrl, icon, updateDesc
optional structured testAccount from secure credential storage
```

It does not create applications (`synchroType=0`), update metadata without a package
(`synchroType=2`), upload dual 32/64-bit APKs, channel APKs, tablet screenshots, schedule an
`onlineTime`, or support Xiaomi's international-store protocol. Those require separate designs
because their inputs, credentials, signatures, and safety properties differ.

## 4. Why Xiaomi cannot use the normal multi-step flow

Huawei, Apple, and Google expose durable remote IDs before final submission. Xiaomi `/dev/push`
combines file transfer and review submission and returns only `result` plus `message`. There is no
upload-only reservation, submission identifier, sandbox, or review-status endpoint.

Therefore:

- `--dry-run` remains fully local and safe;
- `credentials verify` uses only `/dev/query`;
- `--no-submit` is rejected before credentials or network I/O because an upload would already be
  a review submission;
- the full publish command requires the normal explicit confirmation/`--yes`;
- `status --store xiaomi` is explicitly unsupported rather than fabricating a status;
- a push network failure is non-resumable and never automatically replayed.

## 5. Generic atomic-submission safety contract

`StoreCapabilities` gains explicit `atomic_submission` and `supports_no_submit` flags. Existing
stores retain their current defaults. An `AtomicStoreAdapter` adds:

```python
async def publish_atomic(
    *,
    target: StoreTarget,
    artifact: ArtifactInfo,
    release_notes: str | None,
) -> str: ...
```

The publisher verifies the app first, then persists `submission_started` immediately before the
mutation. On success it persists `submitted` and `completed`. A cancellation or network failure
after `submission_started` becomes `submission_uncertain`, which is not resumable. A hard process
crash leaves `submission_started`, also not resumable.

Receipt schema v5 makes the new state semantics explicit and migrates v1-v4 receipts without
changing their existing fields. The repository blocks another publish for the same app/artifact
while a `submission_started` or `submission_uncertain` receipt exists. The operator must inspect
Xiaomi's console and deliberately delete that local receipt before authorizing another push.

This favors a possible manual false positive over an automatic duplicate submission.

## 6. Public configuration and local assets

Configuration schema remains version `1`:

```yaml
version: 1
apps:
  wallet:
    package_name: com.example.wallet
    stores:
      xiaomi:
        credential_profile: xiaomi-wallet
        app_name: Example Wallet
        icon: assets/xiaomi-icon.png
        privacy_url: https://example.com/privacy
        language: zh-CN
```

The icon path is resolved relative to `storehelper.yaml`, not the shell's working directory. It is
public release material, while API secrets and test accounts remain outside YAML. A store-local
preflight validates a readable non-empty PNG and computes its digest before any network I/O,
including during `--dry-run`.

`StoreTarget` gains optional public `app_name`, `icon_path`, and `privacy_url` fields. Other stores
leave them unset.

## 7. Credential model

The dedicated `XIAOMI_API` credential contains:

- `username`: Xiaomi developer login email;
- `api_secret`: the generated automatic-publishing private value (never the interactive login
  password in StoreHelper's recommended setup);
- `public_key_certificate`: Xiaomi's X.509 certificate used only for RSA encryption;
- optional `test_accounts`, grouped by locale, with at most five structured accounts and an
  optional audit note of at most 500 characters.

A structured account stores login type `1` (account/password) or `2` (phone/verification code),
the paired account/password values, and optional registration/access code. All sensitive nested
values use secret types and are serialized only into the OS keyring or CI credential input.

Sources use the existing precedence:

1. `STOREHELPER_XIAOMI_CREDENTIALS_FILE`;
2. the complete individual variables `STOREHELPER_XIAOMI_USERNAME`,
   `STOREHELPER_XIAOMI_API_SECRET`, and `STOREHELPER_XIAOMI_PUBLIC_KEY_CERTIFICATE`, plus optional
   `STOREHELPER_XIAOMI_TEST_ACCOUNTS_JSON`;
3. the Xiaomi keyring profile;
4. secure interactive prompt for the three base fields only.

File and individual sources are mutually exclusive. One profile per app is recommended when
review accounts differ.

## 8. Signature and multipart contract

StoreHelper serializes `RequestData` once and sends those exact bytes. The signature plaintext is:

```json
{
  "sig": [
    {"name": "RequestData", "hash": "lowercase-md5"},
    {"name": "apk", "hash": "lowercase-md5"},
    {"name": "icon", "hash": "lowercase-md5"}
  ],
  "password": "api-secret"
}
```

MD5 is used only because Xiaomi's protocol requires it; SHA-256 remains the durable local artifact
identity. The signer reads the certificate's RSA key size, splits UTF-8 plaintext into
`key_size_bytes - 11` chunks, encrypts each chunk with RSA PKCS#1 v1.5, concatenates the encrypted
blocks, and emits lowercase hexadecimal.

`SIG`, API secret, test accounts, request JSON, digests used for signing, and raw vendor bodies are
never persisted. APK and icon handles are streamed through one fixed-host HTTPS multipart POST;
redirects are rejected and mutation requests have no automatic retry.

## 9. Read-only query and push validation

`/dev/query` requires signed `RequestData` containing `packageName` and `userName`. StoreHelper
requires:

- integer `result == 0`;
- a `packageInfo` object for the configured existing package;
- exact returned `packageName` match;
- boolean `updateVersion == true` before a full push.

Read-only query may use a bounded retry for 429/5xx or transport failure. Result `-7` is an app
ownership/claim error. Other documented results are mapped to stable safe errors without echoing
the raw response.

`/dev/push` requires integer `result == 0`. The public message is not used as a durable ID. The
adapter returns the package name as the stable submission reference. A nonzero response is a
deterministic rejection and may be corrected before a new run. A missing response is ambiguous,
becomes `submission_uncertain`, and must be checked manually.

## 10. Recovery, output, and security

- There is no automatic Xiaomi resume after mutation begins.
- `runs show` may display package name, local artifact/icon paths and hashes, timestamps, and the
  uncertain state; it never displays the API secret, certificate plaintext, `SIG`, test accounts,
  request body, or vendor response.
- Redaction recognizes `SIG` and Xiaomi credential field names in addition to existing patterns.
- Deterministic validation/rejection uses existing exit codes; ambiguous transport failures use
  exit code `8` with an instruction to inspect Xiaomi's management console.
- `runs delete` remains local-only and is the explicit operator acknowledgement required before
  retrying an uncertain artifact.

## 11. Testing and completion

Tests use generated RSA X.509 certificates, fake API secrets/accounts, temporary APK/icon files,
and `httpx.MockTransport`. Required coverage includes:

- schema/config/relative-icon validation and prior-store compatibility;
- credential file/env/keyring/prompt precedence and nested secret redaction;
- exact JSON MD5s, dynamic chunking, decryptable RSA ciphertext, and large signature plaintext;
- signed query success/rejection/retry and fixed HTTPS host;
- streamed push fields/files with no replay;
- dry-run zero I/O, no-submit rejection before credential/network access, confirmation, success,
  vendor rejection, cancellation, uncertain response, duplicate blocking, and local deletion;
- unsupported status and non-resumable Xiaomi receipts;
- all Huawei/HarmonyOS/Apple/Google regressions, at least 90% coverage, package build, Twine, and
  exact-wheel five-store offline dry-runs.

No automated check is allowed to call Xiaomi's live-only `/dev/push` endpoint.
