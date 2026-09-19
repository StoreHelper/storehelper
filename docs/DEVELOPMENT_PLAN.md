# StoreHelper Development Plan

This document tracks the public implementation progress for the StoreHelper CLI.
Checkboxes are updated only after the related implementation and verification succeed.

## v0.9.0 artifact-derived release configuration

- [x] Inspect Android APK/AAB, HarmonyOS APP/HAP and Apple IPA package identity locally.
- [x] Keep market-side IDs and release policy in a minimal, secret-free YAML configuration.
- [x] Reject configured package identities or version codes that disagree with the artifact.
- [x] Bind saved runs to the artifact digest and resolved target before resuming.
- [x] Cover all eight adapters with local unit and integration tests.
- [ ] Merge the reviewed candidate to `main` after Python 3.11–3.14 CI succeeds.
- [ ] Publish immutable `v0.9.0` through TestPyPI, PyPI and GitHub Release gates.
- [ ] Confirm an independent `pipx` install and real-vendor acceptance separately.

## v0.8.1 / MCP 0.1.0 release hardening

- [x] CLI project confinement and isolated run history; path/symlink/receipt regression tests.
- [x] MCP streaming output limits, lifecycle cleanup and isolated Python child execution.
- [x] Distribution allowlists, dirty-source packaging tests and archive validation.
- [x] Build-once workflows, release version guards, TestPyPI rehearsals and checksums.
- [x] Registry manifest, ownership validator and protected manual publication workflow.
- [x] CLI Trusted Publishers, release environments, repository protections and public `v0.8.1`.
- [ ] MCP publication and live vendor/client acceptance.

See [RELEASE_READINESS.md](RELEASE_READINESS.md) for verification and remaining gates.

## v0.8.0 HONOR task status

- [x] Task 1: Official API research, supported-scope design, and implementation plan.
- [x] Task 2: Strict existing-application configuration, version code, and APK preflight.
- [x] Task 3: Independent credential sources, token exchange, and fixed-host authentication.
- [x] Task 4: Exact application/detail/current-release reads and conservative status mapping.
- [x] Task 5: Fixed-endpoint streamed APK allocation/upload with durable recovery context.
- [x] Task 6: Reconciled APK binding, localized `newFeature`, and audit submission.
- [x] Task 7: Registry, runtime, CLI, recovery, security suite, and eight-store compatibility.
- [x] Task 8: HONOR documentation, live checklist, examples, and v0.8.0 versioning.
- [x] Task 9: Clean build, coverage, exact-wheel eight-store smoke tests, and final secret review.

## v0.7.0 vivo task status

- [x] Task 1: Strict existing-application configuration, version code, and APK preflight.
- [x] Task 2: Independent credential sources, keyring namespace, and deterministic HMAC signing.
- [x] Task 3: Fixed-host application/access verification and safe response/error mapping.
- [x] Task 4: Streamed APK upload, exact final update form, and review-status mapping.
- [x] Task 5: Staged non-resumable uncertainty boundary and duplicate protection.
- [x] Task 6: Registry, runtime, CLI, security regression suite, and seven-store compatibility.
- [x] Task 7: vivo documentation, live checklist, examples, and v0.7.0 versioning.
- [x] Task 8: Clean build, coverage, exact-wheel seven-store smoke tests, and final secret review.

## v0.6.0 OPPO task status

- [x] Task 1: Staged non-resumable submission orchestration and uncertainty boundary.
- [x] Task 2: Strict existing-application configuration, version code, and APK preflight.
- [x] Task 3: Application-specific credential sources and independent keyring namespace.
- [x] Task 4: Deterministic HMAC-SHA256 signing and memory-only token cache.
- [x] Task 5: Fixed-host token/application access verification and safe error mapping.
- [x] Task 6: Allowlisted dynamic upload host and streamed APK upload with zero mutation retries.
- [x] Task 7: Fresh-listing staging, exact final update form, and review-status mapping.
- [x] Task 8: Registry, runtime, CLI, uncertain duplicate blocking, and security regression suite.
- [x] Task 9: OPPO documentation, examples, and v0.6.0 versioning.
- [x] Task 10: Clean build, coverage, exact-wheel six-store smoke tests, and final secret review.

## v0.5.0 Xiaomi task status

- [x] Task 1: Atomic submission safety and receipt schema v5.
- [x] Task 2: Strict existing-package configuration, relative PNG icon, and APK preflight.
- [x] Task 3: Dedicated API credential sources, keyring namespace, and structured review accounts.
- [x] Task 4: Protocol-required streamed MD5 and dynamic RSA PKCS#1 v1.5 encryption.
- [x] Task 5: Fixed-host signed read-only package/access verification.
- [x] Task 6: Single-APK streamed upload-and-review submission with zero mutation retries.
- [x] Task 7: Registry, runtime, CLI, and store-aware credential commands.
- [x] Task 8: Uncertain-result blocking, manual acknowledgement, and security regression suite.
- [x] Task 9: Xiaomi documentation, examples, and v0.5.0 versioning.
- [x] Task 10: Clean build, coverage, exact-wheel five-store smoke tests, and final secret review.

## v0.4.0 Google Play task status

- [x] Task 1: Durable App Edit operation context and receipt schema v4.
- [x] Task 2: Strict Google package/track/draft-or-completed configuration.
- [x] Task 3: Dedicated service-account credential sources and keyring namespace.
- [x] Task 4: RS256 OAuth assertion, token exchange, and in-memory cache.
- [x] Task 5: Read-only application/track verification and lifecycle status mapping.
- [x] Task 6: Streamed AAB/APK upload with exact SHA-256 and `versionCode` validation.
- [x] Task 7: Safe, idempotent Track preparation with optional localized notes.
- [x] Task 8: Explicit Edit validation/commit and lifecycle reconciliation.
- [x] Task 9: Registry, runtime, CLI, JSON output, and store-aware credential commands.
- [x] Task 10: End-to-end interruption, recovery, migration, and security regression suite.
- [x] Task 11: Google Play documentation, examples, and v0.4.0 versioning.
- [x] Task 12: Clean build, coverage, exact-wheel smoke tests, and final secret review.

## v0.3.0 Apple App Store task status

- [x] Task 1: Store-neutral credential and receipt schema v3 preparation.
- [x] Task 2: Strict Apple target configuration and resolution.
- [x] Task 3: Team/individual App Store Connect API-key handling and ES256 JWTs.
- [x] Task 4: Safe IPA validation and metadata inspection.
- [x] Task 5: Read-only app/version/bundle/platform verification.
- [x] Task 6: Native Build Upload reservation, streamed range transfer, and commit.
- [x] Task 7: Processing polling and durable Build-ID recovery.
- [x] Task 8: Build attachment and optional localized `whatsNew` update.
- [x] Task 9: Modern review-submission reconciliation and status mapping.
- [x] Task 10: Store-aware CLI and Apple credential commands.
- [x] Task 11: End-to-end interruption, recovery, and security regression suite.
- [x] Task 12: Apple documentation, examples, and v0.3.0 versioning.
- [x] Task 13: Clean build, coverage, installed-wheel smoke, and final secret review.

## v0.2.0 multi-store and HarmonyOS task status

- [x] Task 1: Store-neutral models, errors, and Huawei compatibility.
- [x] Task 2: Strict multi-store configuration and resolved targets.
- [x] Task 3: Receipt schema v2 and safe migration.
- [x] Task 4: Store-neutral publishing state machine.
- [x] Task 5: HarmonyOS APP/HAP artifact validation.
- [x] Task 6: HarmonyOS authenticated API client and app verification.
- [x] Task 7: Secure streamed OBS upload and package binding.
- [x] Task 8: HarmonyOS processing, release notes, submission, and status.
- [x] Task 9: Static adapter registry, runtime factory, and CLI integration.
- [x] Task 10: End-to-end recovery and security regression suite.
- [x] Task 11: Documentation and public progress tracking.
- [x] Task 12: Full verification and milestone closure.

## Implementation task status

- [x] Task 1: Package foundation and domain contract.
- [x] Task 2: Strict multi-application configuration.
- [x] Task 3: Secure Huawei Service Account credentials.
- [x] Task 4: APK/AAB validation and logical file naming.
- [x] Task 5: PS256 authentication and Huawei error mapping.
- [x] Task 6: Sanitized Huawei upload and binding client.
- [x] Task 7: Compile polling, release notes, submission, and status.
- [x] Task 8: Atomic redacted run receipts.
- [x] Task 9: Publishing orchestration and recovery.
- [x] Task 10: Output renderers and foundational CLI commands.
- [x] Task 11: Publish, resume, status, and run commands.
- [x] Task 12: CI, documentation, build, and installed CLI verification.

## Milestone 0: Product and architecture

- [x] Select Python 3.11+ as the implementation language.
- [x] Limit the first store integration to Huawei AppGallery Android Publishing API v2.
- [x] Limit package support to existing Huawei applications and APK/AAB files.
- [x] Approve the modular single-package architecture.
- [x] Define the local-first credential, configuration, output, and recovery policies.
- [x] Define the publishing state machine and polling behavior.
- [x] Write the implementation-ready design specification.
- [x] Write and self-review the task-level implementation plan.

## Milestone 1: Project foundation

- [x] Create the Python package and `storehelper` console entry point.
- [x] Configure Ruff, mypy, pytest, coverage, and build tooling.
- [x] Add GitHub Actions for Python 3.11-3.14.
- [x] Add contributor development commands and repository metadata.

## Milestone 2: Configuration and credentials

- [x] Implement strict `storehelper.yaml` parsing and validation.
- [x] Implement multi-application selection.
- [x] Implement `storehelper init` and `storehelper config validate`.
- [x] Import Huawei Service Account JSON into the operating-system keyring.
- [x] Load credentials from CI environment variables or a CI secret file.
- [x] Fall back to secure interactive input only in an interactive terminal.
- [x] Implement credential listing, verification, and deletion without exposing secrets.

## Milestone 3: Huawei publishing adapter

- [x] Generate and normalize Huawei PS256 Service Account JWTs.
- [x] Validate credentials against a read-only Huawei endpoint.
- [x] Validate APK/AAB files and generate safe logical file names.
- [x] Request a Huawei upload URL and stream the package upload.
- [x] Bind the uploaded package and capture `pkgVersion`.
- [x] Poll package compilation with documented status mapping.
- [x] Update only the selected language's release notes.
- [x] Submit the package for Huawei review.
- [x] Query the current Huawei review state.
- [x] Translate Huawei errors into safe, actionable StoreHelper errors.

## Milestone 4: Orchestration and recovery

- [x] Implement `publish`, `--dry-run`, `--no-submit`, and `--yes`.
- [x] Persist redacted run receipts atomically in platform-specific state directories.
- [x] Detect an unfinished run for the same app and package SHA-256.
- [x] Implement `resume`, `status`, and `runs` commands.
- [x] Handle timeout and interruption as resumable outcomes.
- [x] Keep machine JSON on stdout and progress or diagnostics on stderr.
- [x] Implement stable result schema version 1 and documented exit codes.

## Milestone 5: Quality and security verification

- [x] Cover configuration, credentials, JWT, package, state, and renderer behavior with unit tests.
- [x] Cover the Huawei HTTP contract with mocked integration tests based on complete documented responses.
- [x] Cover full publish, timeout, resume, no-submit, dry-run, and JSON CLI flows.
- [x] Verify secrets and temporary upload values are redacted from logs, receipts, and JSON.
- [x] Pass formatting, linting, static typing, tests, coverage, package build, and CLI smoke tests.

## Milestone 6: Documentation and distribution

- [x] Replace the placeholder README with installation, configuration, security, and usage guides.
- [x] Add a redacted example configuration and release-notes example.
- [x] Document pipx installation and source installation.
- [x] Add an automated PyPI Trusted Publishing workflow.
- [x] Add GitHub Release artifacts and checksums.
- [x] Plan the separate `StoreHelper/homebrew-tap` repository after the first tagged release.
- [x] Prepare the `v0.1.0` release checklist.

## Milestone 7: Multi-store foundation and HarmonyOS

- [x] Replace Huawei-specific orchestration types with store-neutral contracts and capabilities.
- [x] Add a static audited registry and runtime selection for `huawei` and `harmonyos`.
- [x] Preserve Huawei-only configuration and migrate v1 receipts to generic v2 receipts.
- [x] Validate HarmonyOS APP/HAP archives locally without extraction or vendor tooling.
- [x] Verify an existing HarmonyOS application through package type `7` lookup.
- [x] Allocate an OBS upload, stream exact signed headers, and persist only `packageId`.
- [x] Poll processing, update only `newFeatures`, submit review, and query review state.
- [x] Resume timeout and interruption without re-upload and handle `204144727` eventual consistency.
- [x] Document dual-store setup, shared credentials, security boundaries, and opt-in live checks.
- [x] Complete the clean v0.2.0 build, coverage, installed-wheel, smoke, and secret review gates.

## Later milestones

- [x] Complete the Apple App Store Connect v0.3.0 implementation milestone.
- [x] Complete the Google Play v0.4.0 verification and distribution milestone.
- [x] Complete the Xiaomi v0.5.0 implementation and security milestone.
- [x] Complete the OPPO v0.6.0 implementation and security milestone.
- [x] Complete the vivo v0.7.0 implementation and security milestone.
- [x] Complete the HONOR v0.8.0 implementation and security milestone.
- [ ] `storehelper-mcp` using the stable Python API and JSON result schema.
- [ ] GitHub Action, Flutter tooling, and IDE integrations.
