# StoreHelper Development Plan

This document tracks the public implementation progress for the StoreHelper CLI.
Checkboxes are updated only after the related implementation and verification succeed.

## v0.2.0 multi-store and HarmonyOS task status

- [x] Task 1: Store-neutral models, errors, and Huawei compatibility.
- [x] Task 2: Strict multi-store configuration and resolved targets.
- [ ] Task 3: Receipt schema v2 and safe migration.
- [ ] Task 4: Store-neutral publishing state machine.
- [ ] Task 5: HarmonyOS APP/HAP artifact validation.
- [ ] Task 6: HarmonyOS authenticated API client and app verification.
- [ ] Task 7: Secure streamed OBS upload and package binding.
- [ ] Task 8: HarmonyOS processing, release notes, submission, and status.
- [ ] Task 9: Static adapter registry, runtime factory, and CLI integration.
- [ ] Task 10: End-to-end recovery and security regression suite.
- [ ] Task 11: Documentation and public progress tracking.
- [ ] Task 12: Full verification and milestone closure.

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

## Later milestones

- [ ] Huawei HarmonyOS adapter.
- [ ] Apple App Store Connect adapter.
- [ ] Additional Android store adapters.
- [ ] `storehelper-mcp` using the stable Python API and JSON result schema.
- [ ] GitHub Action, Flutter tooling, and IDE integrations.
