# StoreHelper Multi-Store Foundation and HarmonyOS Design

**Status:** Approved for implementation
**Date:** 2026-08-03
**Target release:** v0.2.0

## 1. Purpose

This increment converts the Huawei-only StoreHelper CLI into a stable multi-store publishing core and adds end-to-end publishing for existing HarmonyOS applications in AppGallery Connect. It preserves the local-first security model, resumable run receipts, text and JSON output, and reusable async Python API established by v0.1.0.

The implementation is original open-source code informed by Huawei's public AppGallery Connect documentation and verified protocol behavior in the owner's private reference projects. It must not copy private infrastructure, organization-specific data, credentials, endpoints, or business rules.

## 2. Product scope

### Goals

- Replace Huawei-specific orchestration types with store-neutral contracts.
- Register adapters statically so supported stores are explicit and auditable.
- Add `harmonyos` as a CLI store alongside `huawei`.
- Publish `.app` and `.hap` artifacts to applications that already exist in AppGallery Connect.
- Reuse Huawei Service Account credentials without duplicating secrets.
- Configure HarmonyOS `app_id`, `package_name`, `credential_profile`, and `language` locally.
- Update only `newFeatures`; do not overwrite existing AppGallery metadata.
- Stream packages to the Huawei-provided OBS URL and never persist temporary upload material.
- Poll asynchronous package parsing with the existing configurable interval and deadline instead of a fixed sleep.
- Resume safely after interruption, timeout, or Huawei eventual-consistency responses.
- Keep Huawei Android behavior and existing v0.1.0 configuration and receipts readable.
- Establish the architecture used by later Apple, Google Play, Xiaomi, OPPO, vivo, and Honor adapters.

### Non-goals

- Creating HarmonyOS applications or developer accounts.
- Managing screenshots, descriptions, names, categories, countries, privacy policies, pricing, signing, phased release, or audit cancellation.
- Publishing HarmonyOS atomic services.
- Extracting or validating every field in a HarmonyOS manifest.
- Dynamic third-party Python plugins or entry-point discovery.
- Adding Apple or another Android vendor in this increment.
- Waiting for human review to finish during `publish`.
- Persisting OBS URLs, authorization headers, object IDs, JWTs, or private keys.

## 3. Confirmed implementation order

The overall adapter rollout is:

1. Multi-store foundation.
2. HarmonyOS.
3. Apple App Store Connect.
4. Google Play.
5. Xiaomi.
6. OPPO.
7. vivo.
8. Honor.

Each adapter is completed with tests, documentation, and progress tracking before the next adapter begins. The MCP server, GitHub Action, Flutter tooling, and IDE plugins consume the stable Python API and JSON result schema after the CLI adapters are mature.

## 4. Architecture

The CLI remains a modular single Python package. Store-neutral orchestration owns sequencing and recovery. A static registry owns supported-store discovery and construction. Each adapter owns vendor protocol, capabilities, artifact validation, and error translation.

```text
CLI
  -> RuntimeFactory
       -> Application/store configuration
       -> CredentialResolver (credential kind)
       -> AdapterRegistry (store name)
            -> HuaweiAndroidAdapter
            -> HarmonyOSAdapter
  -> Publisher (store-neutral state machine)
       -> StoreAdapter
       -> RunRepository
  -> text or JSON renderer
```

Dependencies point toward domain contracts. The publisher must not import `stores.huawei` or `stores.harmonyos`. An adapter must not read CLI input, select configuration, render output, or write a run receipt.

### 4.1 Static adapter registry

`stores/registry.py` contains an immutable registration for every built-in store. A registration declares:

- canonical store name;
- human-readable label;
- credential kind;
- artifact suffixes;
- publishing capabilities;
- artifact validator;
- adapter factory.

The initial store names are `huawei` and `harmonyos`. Unknown names fail before credential lookup or network access. Registration is static and internal; arbitrary installed packages cannot execute adapter code.

### 4.2 Capability declaration

`StoreCapabilities` describes behavior that changes orchestration:

- `requires_processing_poll`;
- `requires_release_notes`;
- `supports_review_status`;
- accepted artifact suffixes.

Huawei Android and HarmonyOS both require processing polling, release notes, and review status. Later stores may report an artifact ready immediately. Capability checks replace store-name conditionals.

### 4.3 Store-neutral contracts

Shared types live under `stores/models.py` and `artifacts/models.py`:

- `StoreName` enumerates built-in stores.
- `StoreTarget` contains resolved app ID, package name, language, and credential profile.
- `ArtifactInfo` contains only local path, SHA-256, size, suffix, and safe upload name.
- `VerifiedApplication` is a minimal verified identity.
- `UploadedArtifact` contains a durable vendor tracking ID.
- `ProcessingState` is `processing`, `ready`, or `failed`.
- `ReviewState` normalizes vendor review states.

The adapter protocol exposes `verify`, `upload`, `processing_status`, `update_release_notes`, `submit`, and `review_status`. The publisher calls `processing_status` only when the capability requires it.

Vendor errors derive from a shared safe error base. A dedicated `ArtifactStillProcessingError` communicates eventual consistency without comparing Huawei-specific error codes inside the publisher.

## 5. Configuration

Configuration remains strict schema version 1. Existing Huawei-only files remain valid. HarmonyOS adds a store-local package name because a product's Android package and HarmonyOS bundle name may differ.

```yaml
version: 1

apps:
  wallet:
    package_name: com.example.wallet # v0.1 Huawei default, retained for compatibility
    stores:
      huawei:
        app_id: "100000001"
        credential_profile: company
        language: zh-CN
      harmonyos:
        app_id: "100000002"
        package_name: com.example.wallet.harmony
        credential_profile: company
        language: zh-CN
```

Rules:

- `stores.huawei` and `stores.harmonyos` are optional individually, but at least one store is required.
- `apps.<alias>.package_name` remains required in schema version 1 for backward compatibility.
- HarmonyOS requires its own non-empty `package_name`.
- `app_id` and `credential_profile` are required.
- `language` defaults to `zh-CN`.
- Unknown fields and secret-like keys remain rejected.
- Selecting a store not configured for the chosen application is a usage error.
- `storehelper init` shows both stores with HarmonyOS commented or documented as optional.

A future schema version may move every platform identity under its store configuration. This increment does not silently reinterpret existing Huawei configuration.

## 6. Credentials

Huawei Android and HarmonyOS use the same `huawei_service_account` credential kind:

- `key_id`;
- `sub_account`;
- `private_key`;
- optional `token_uri`.

Existing keyring entries under service `storehelper:huawei` and existing CI environment variables continue to work. HarmonyOS does not require a duplicate import command or duplicate keyring value. The runtime registry maps both stores to the Huawei credential resolver.

The CLI continues to expose `credentials ... huawei`, because that command names the credential provider rather than a publishing store. Help text must state that the profile is shared by Huawei Android and HarmonyOS.

## 7. Generic publishing and recovery

The state sequence remains:

```text
created -> validated -> app_verified -> package_bound
        -> package_compiling -> package_ready
        -> metadata_updated -> submitted -> completed
```

`timed_out` and `interrupted` are resumable when a durable artifact ID exists. `failed` is terminal.

The publisher receives a resolved `StoreRuntime` containing the adapter, target, validator, and capabilities. It never reads a Huawei field directly.

### Receipt compatibility

New run receipts use schema version 2 and rename the Huawei-specific persisted field `pkg_version` to `artifact_id`. The repository accepts schema version 1 receipts, migrates `pkg_version` to `artifact_id` in memory, and writes schema version 2 on the next transition. The serialized state value `package_compiling` is retained for compatibility even though it represents generic vendor-side artifact processing. The artifact ID value is:

- Huawei Android: `pkgVersion`;
- HarmonyOS: `packageId`.

Receipts include the actual store name. Duplicate detection keys remain `(store, app_id, artifact_sha256)`. A run may resume only when its store, app ID, package name, and local artifact digest match the selected configuration.

Temporary `objectId`, OBS URL, OBS headers, token, and authorization values never enter a receipt, result, exception message, or log.

## 8. HarmonyOS artifact validation

Accepted files are `.app` and `.hap`, case-insensitively, with a maximum size of 4 GiB.

Validation performs:

- regular-file and non-empty checks;
- suffix allowlist;
- streaming SHA-256 calculation;
- ZIP integrity and unsafe-member-name checks;
- for `.app`, require `pack.info` and at least one embedded `.hap` member;
- for `.hap`, require a valid non-empty ZIP but avoid over-constraining compiled manifest layout;
- generate a bounded ASCII-safe upload name while preserving `.app` or `.hap`.

Validation does not unpack files onto disk or execute a vendor tool. It returns bounded errors without dumping archive contents.

## 9. HarmonyOS vendor protocol

### 9.1 Authentication and application verification

HarmonyOS reuses the Huawei PS256 Service Account JWT implementation.

Application verification calls the v2 application ID list endpoint with the configured package name and `packageTypes=7`. The configured `app_id` must match a returned ID. The parser accepts Huawei's observed `appids[].value` representation in addition to safe compatible scalar or `appId` representations.

The CLI never creates a missing application. A missing or mismatched application produces an actionable configuration error.

### 9.2 OBS upload and package binding

1. Request an upload allocation from v2 `upload-url/for-obs` with `appId`, exact safe `fileName`, `contentLength`, and `releaseType=1`.
2. Validate the returned URL is HTTPS, contains no embedded credentials, and uses the returned `PUT` method.
3. Stream the file directly to the temporary OBS URL using exactly the returned signed headers. Do not attach the Huawei API bearer token and do not follow redirects.
4. Accept HTTP 200 or 204 from OBS.
5. Bind the object using v3 `PUT app-package-info` with `appId`, `releaseType=1`, `fileName`, and `objectId`.
6. Require a non-empty `packageId` and expose only that value as the durable `artifact_id`.

OBS URLs, headers, signatures, and object IDs are redacted and omitted from diagnostic payloads. Upload reads are streamed; a 4 GiB package is never loaded fully into memory.

### 9.3 Package parsing

The v2 package-info endpoint is queried with `appId` and `packageId`.

The public response shape is not sufficiently stable to make one undocumented numeric field a permanent contract. Parsing therefore follows conservative rules:

- an explicit known failure result becomes `failed` with a bounded safe reason;
- an absent or empty `packageInfo` becomes `processing`;
- a successful response with populated package information becomes `ready`;
- a recognized explicit processing state remains `processing`;
- an unknown explicit terminal state raises a safe protocol error instead of assuming success.

Default polling is every 15 seconds for at most 10 minutes. HTTP 429 honors `Retry-After`; transient 5xx errors retry within the same deadline; one 401/403 refresh is allowed. A submit response meaning "package is still being processed" raises `ArtifactStillProcessingError`, returns to the processing state, and remains resumable.

### 9.4 Release notes and submission

Only v3 `PUT app-language-info` is called, with:

```json
{
  "lang": "zh-CN",
  "newFeatures": "..."
}
```

`lang` comes from configuration and `newFeatures` from the CLI release notes. Notes must contain 1 to 500 characters. No app name, description, privacy policy, category, screenshots, or country data is sent.

Submission calls v3 `POST app-submit` for a formal full release (`releaseType=1`, `releasePhase=0`) and includes only fields required by the verified protocol. The publish operation finishes when AppGallery accepts the review request; it does not wait for human review.

### 9.5 Review status

The v3 application-info endpoint is queried with the configured application identity. Known `releaseState` values map to normalized states:

- approved;
- rejected;
- in review;
- pending;
- unknown.

Raw vendor payloads are not persisted. Rejection text is not needed for this increment unless it can be returned safely and within a bounded length.

## 10. CLI and output

Existing commands remain stable:

```bash
storehelper publish --app wallet --store harmonyos --file build/app-release.app \
  --release-notes-file RELEASE_NOTES.md --yes
storehelper publish --app wallet --store harmonyos --file build/entry.hap --no-submit
storehelper resume RUN_ID
storehelper status --app wallet --store harmonyos
```

`--store` accepts only registered names and reports configured choices in help. `resume` derives the store from the receipt, then verifies that the current application configuration still contains a matching target. Text messages use the selected store label. Operation-result JSON preserves schema version 1 and emits `store: "harmonyos"` for HarmonyOS operations. Run-receipt schema versioning is internal local state and advances independently to version 2.

Exit-code categories remain stable: usage, configuration, credentials, package validation, vendor rejection, network/timeout, local state, and internal error.

## 11. Security controls

- Keep secret material only in environment memory, the selected secret file, or OS keyring.
- Reuse the existing redaction boundary for PEM, JWT, bearer, password, and authorization values.
- Add redaction coverage for OBS query strings, signed headers, AWS authorization values, and object IDs.
- Never log full Huawei response payloads from upload allocation or binding.
- Reject non-HTTPS temporary upload URLs, embedded URL credentials, redirects, and non-PUT methods.
- Stream file content and close handles deterministically.
- Bound vendor error messages and archive-validation messages.
- Write receipts atomically with user-only permissions.
- Keep configuration secret scanning recursive and strict.

## 12. Testing and acceptance criteria

### Unit tests

- Store enum, capabilities, registration, and unknown-store rejection.
- Backward-compatible Huawei configuration plus strict HarmonyOS configuration.
- Credential-kind sharing between Huawei Android and HarmonyOS.
- Version 1 `pkg_version` receipt migration to version 2 `artifact_id` output.
- Store-neutral publishing state transitions and resumption.
- `.app` and `.hap` validation, archive integrity, traversal rejection, size limits, hashing, and safe names.
- HarmonyOS response parsing, package-processing mapping, review-state mapping, and redaction.

### Mocked integration tests

- Service Account authentication and app ID verification with `packageTypes=7`.
- Upload allocation, streamed OBS PUT, package binding, and `packageId` capture.
- No bearer header on OBS and exact signed-header use.
- Processing poll, release-note-only update, submission, status query, timeout, and resume.
- Eventual-consistency submit response returns to polling without re-uploading.
- CLI text and JSON for Huawei Android and HarmonyOS.

### Required verification

- Ruff formatting and linting pass.
- mypy passes in strict project mode.
- all tests pass on supported Python versions;
- statement coverage remains at least 90%;
- source and wheel builds succeed;
- installed `storehelper --help`, `version`, config validation, and dry-run smoke tests succeed;
- a repository-wide secret scan finds no private reference values or temporary upload data.

No real AppGallery mutation is executed by automated tests. A real end-to-end submission is a documented manual verification using the user's own test application and credentials.

## 13. Documentation deliverables

- Update the public development plan with adapter rollout and completion checkboxes.
- Update README examples for both Huawei Android and HarmonyOS.
- Update the redacted example configuration.
- Document shared credentials, `.app`/`.hap` validation, no-submit, timeout, and resume.
- Add a manual HarmonyOS verification checklist.
- Record the receipt compatibility rule and security guarantees.

## 14. Design review

The design intentionally keeps one state machine and one result contract. It avoids two common failure modes: duplicating orchestration per vendor and making the generic layer depend on vendor field names. Static registration is preferred over plugins until multiple adapters prove the contract. HarmonyOS metadata updates are deliberately narrow because overwriting a complete AppGallery listing from a package-upload CLI would be unsafe.

The main remaining protocol uncertainty is the detailed package-info processing response. The design contains that uncertainty inside the HarmonyOS parser, treats missing information conservatively, and uses the documented/observed submit response as a recoverable consistency signal. This can be extended without changing the public CLI, receipt, or publisher contracts.
