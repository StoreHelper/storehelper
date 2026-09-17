# Artifact-derived publishing configuration

## Goal and scope

StoreHelper's generated YAML should contain only values that a package cannot supply reliably. A release must derive package or bundle identity and applicable version code from the selected APK, AAB, IPA, APP, or HAP before any credential or network operation. Existing version-1 YAML remains valid; explicitly configured package fields become assertions against readable artifact metadata. This change does not infer market accounts, choose a market, fetch missing Apple version resource IDs, or submit without the existing confirmation policy.

## User workflow

`storehelper init` creates a minimal Huawei configuration rather than an eight-market catalogue. `storehelper init --store apple --file build/app.ipa` creates only the selected store entry and confirms that the artifact can be inspected; `--file` does not copy package-derived identifiers into YAML. Users may add other store entries under the same app alias. The file is never overwritten. `storehelper config validate` validates the YAML alone and does not claim the account or artifact is valid.

`storehelper publish --app APP --store STORE --file PACKAGE --dry-run` reads package metadata locally and resolves the market target before credentials, network access, or run-record writes. When a configured package/bundle/version is present, a disagreement with the artifact is an error. When metadata is unavailable, a legacy configured value may be used; if neither source provides a required value, the command fails locally with a message naming the missing field. Market identifiers, credential profile, release track/status, privacy URL, market icon, and review text remain explicit. No secret enters YAML.

Read-only `credentials verify` and `status` retain their no-file behavior for old complete YAML. New minimal YAML supports `--file` for first use; without it, a unique matching local receipt may supply the package identity, but zero or conflicting identities require `--file`. `resume` uses the saved, hash-pinned artifact and receipt identity and rejects conflicting YAML. These paths never guess an app from a credential profile.

## Artifact metadata boundary

Inspection returns a typed, secret-free identity record. APK uses the compiled Android manifest; AAB uses the base module's protobuf manifest; IPA uses its sole top-level `Payload/*.app/Info.plist` (already parsed); APP uses top-level `pack.info`; HAP uses top-level `module.json`. Android/Harmony metadata parsing is separate from existing structural validators, whose sparse test artifacts stay valid for legacy configured flows. Only bounded metadata members are read; duplicate or ambiguous metadata, oversized members, invalid encoding, and unsupported shapes are not silently accepted as a different identity. No archive contents are executed or extracted to the filesystem. Package metadata is never treated as proof of market ownership; the adapter's read-only account/app verification remains authoritative.

## Compatibility and recovery

The configuration schema remains `version: 1`. `package_name`, HarmonyOS `package_name`, Apple `bundle_id`, and OPPO/vivo/HONOR `version_code` become optional, while their supplied values remain strictly validated. Old YAML works without migration. Target resolution produces the same complete `StoreTarget` shape as before. Package-derived values are checked before mutable vendor calls. Receipts retain resolved identity and target policy; any new optional version-code receipt field migrates old receipts safely. A retry uses the original artifact path and SHA-256, not a newly selected package. Apple-only YAML no longer needs a dummy Android package name.

## Validation

Test each format's positive extraction with small generated fixtures and failures for malformed, conflicting, duplicate, and oversized metadata. Test minimal YAML, old YAML, mismatch rejection before credentials/network, `--file` read-only commands, receipt ambiguity, and resume. Run the full Python suite, Ruff, mypy, build checks, and packaging validation. The pre-change suite has one environment-dependent version test failure because the shared `.venv` has `storehelper 0.8.0` distribution metadata; use a fresh editable environment for the final full run.
