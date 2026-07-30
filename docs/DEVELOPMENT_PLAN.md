# StoreHelper Development Plan

This document tracks the public implementation progress for the StoreHelper CLI.
Checkboxes are updated only after the related implementation and verification succeed.

## Implementation task status

- [x] Task 1: Package foundation and domain contract.
- [ ] Task 2: Strict multi-application configuration.
- [ ] Task 3: Secure Huawei Service Account credentials.
- [ ] Task 4: APK/AAB validation and logical file naming.
- [ ] Task 5: PS256 authentication and Huawei error mapping.
- [ ] Task 6: Sanitized Huawei upload and binding client.
- [ ] Task 7: Compile polling, release notes, submission, and status.
- [ ] Task 8: Atomic redacted run receipts.
- [ ] Task 9: Publishing orchestration and recovery.
- [ ] Task 10: Output renderers and foundational CLI commands.
- [ ] Task 11: Publish, resume, status, and run commands.
- [ ] Task 12: CI, documentation, build, and installed CLI verification.

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

- [ ] Create the Python package and `storehelper` console entry point.
- [x] Configure Ruff, mypy, pytest, coverage, and build tooling.
- [ ] Add GitHub Actions for Python 3.11-3.14.
- [ ] Add contributor development commands and repository metadata.

## Milestone 2: Configuration and credentials

- [ ] Implement strict `storehelper.yaml` parsing and validation.
- [ ] Implement multi-application selection.
- [ ] Implement `storehelper init` and `storehelper config validate`.
- [ ] Import Huawei Service Account JSON into the operating-system keyring.
- [ ] Load credentials from CI environment variables or a CI secret file.
- [ ] Fall back to secure interactive input only in an interactive terminal.
- [ ] Implement credential listing, verification, and deletion without exposing secrets.

## Milestone 3: Huawei publishing adapter

- [ ] Generate and normalize Huawei PS256 Service Account JWTs.
- [ ] Validate credentials against a read-only Huawei endpoint.
- [ ] Validate APK/AAB files and generate safe logical file names.
- [ ] Request a Huawei upload URL and stream the package upload.
- [ ] Bind the uploaded package and capture `pkgVersion`.
- [ ] Poll package compilation with documented status mapping.
- [ ] Update only the selected language's release notes.
- [ ] Submit the package for Huawei review.
- [ ] Query the current Huawei review state.
- [ ] Translate Huawei errors into safe, actionable StoreHelper errors.

## Milestone 4: Orchestration and recovery

- [ ] Implement `publish`, `--dry-run`, `--no-submit`, and `--yes`.
- [ ] Persist redacted run receipts atomically in platform-specific state directories.
- [ ] Detect an unfinished run for the same app and package SHA-256.
- [ ] Implement `resume`, `status`, and `runs` commands.
- [ ] Handle timeout and interruption as resumable outcomes.
- [ ] Keep machine JSON on stdout and progress or diagnostics on stderr.
- [ ] Implement stable result schema version 1 and documented exit codes.

## Milestone 5: Quality and security verification

- [ ] Cover configuration, credentials, JWT, package, state, and renderer behavior with unit tests.
- [ ] Cover the Huawei HTTP contract with mocked integration tests based on complete documented responses.
- [ ] Cover full publish, timeout, resume, no-submit, dry-run, and JSON CLI flows.
- [ ] Verify secrets and temporary upload values are redacted from logs, receipts, and JSON.
- [ ] Pass formatting, linting, static typing, tests, coverage, package build, and CLI smoke tests.

## Milestone 6: Documentation and distribution

- [ ] Replace the placeholder README with installation, configuration, security, and usage guides.
- [ ] Add a redacted example configuration and release-notes example.
- [ ] Document pipx installation and source installation.
- [ ] Add an automated PyPI Trusted Publishing workflow.
- [ ] Add GitHub Release artifacts and checksums.
- [ ] Plan the separate `StoreHelper/homebrew-tap` repository after the first tagged release.
- [ ] Prepare the `v0.1.0` release checklist.

## Later milestones

- [ ] Huawei HarmonyOS adapter.
- [ ] Apple App Store Connect adapter.
- [ ] Additional Android store adapters.
- [ ] `storehelper-mcp` using the stable Python API and JSON result schema.
- [ ] GitHub Action, Flutter tooling, and IDE integrations.
