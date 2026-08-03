from pathlib import Path
from typing import get_type_hints

from storehelper.artifacts.models import ArtifactInfo
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.base import StoreAdapter
from storehelper.stores.errors import ArtifactStillProcessingError, StoreVendorError
from storehelper.stores.models import (
    CredentialKind,
    ProcessingState,
    ProcessingStatus,
    ReviewStatus,
    StoreCapabilities,
    StoreName,
    StoreTarget,
    UploadedArtifact,
    VerifiedApplication,
)


def test_store_target_serializes_a_harmonyos_identity() -> None:
    target = StoreTarget(
        store=StoreName.HARMONYOS,
        label="Huawei AppGallery (HarmonyOS)",
        app_id="100000002",
        package_name="com.example.wallet.harmony",
        credential_profile="company",
        language="zh-CN",
    )

    assert target.model_dump(mode="json") == {
        "store": "harmonyos",
        "label": "Huawei AppGallery (HarmonyOS)",
        "app_id": "100000002",
        "package_name": "com.example.wallet.harmony",
        "credential_profile": "company",
        "language": "zh-CN",
    }


def test_capabilities_describe_orchestration_without_vendor_fields() -> None:
    capabilities = StoreCapabilities(
        credential_kind=CredentialKind.HUAWEI_SERVICE_ACCOUNT,
        artifact_suffixes=(".app", ".hap"),
        requires_processing_poll=True,
        requires_release_notes=True,
        supports_review_status=True,
    )

    assert capabilities.model_dump(mode="json") == {
        "credential_kind": "huawei_service_account",
        "artifact_suffixes": [".app", ".hap"],
        "requires_processing_poll": True,
        "requires_release_notes": True,
        "supports_review_status": True,
    }


def test_generic_artifact_and_vendor_results_have_no_huawei_field_names() -> None:
    artifact = ArtifactInfo(
        path=Path("/tmp/release.app"),
        kind="app",
        size=123,
        sha256="a" * 64,
        logical_name="release.app",
    )
    verified = VerifiedApplication(
        app_id="100000002",
        package_name="com.example.wallet.harmony",
    )
    uploaded = UploadedArtifact(artifact_id="package-42")
    processing = ProcessingStatus(state=ProcessingState.PROCESSING)

    assert artifact.kind == "app"
    assert artifact.warnings == ()
    assert verified.app_id == "100000002"
    assert uploaded.artifact_id == "package-42"
    assert processing.state is ProcessingState.PROCESSING
    assert ReviewStatus.IN_REVIEW.value == "in_review"


def test_processing_error_is_store_neutral_resumable_and_redacted() -> None:
    error = ArtifactStillProcessingError(
        "HARMONYOS_PACKAGE_PROCESSING",
        "Authorization: Bearer aaa.bbb.ccc",
        vendor_code="204144727",
    )

    assert isinstance(error, StoreVendorError)
    assert error.exit_code is ExitCode.RESUMABLE_TIMEOUT
    assert error.resumable is True
    assert error.vendor_code == "204144727"
    assert "aaa.bbb.ccc" not in str(error)


def test_adapter_protocol_uses_store_neutral_boundary_types() -> None:
    verify_hints = get_type_hints(StoreAdapter.verify)
    upload_hints = get_type_hints(StoreAdapter.upload)
    processing_hints = get_type_hints(StoreAdapter.processing_status)

    assert verify_hints["return"] is VerifiedApplication
    assert upload_hints["artifact"] is ArtifactInfo
    assert upload_hints["return"] is UploadedArtifact
    assert processing_hints["artifact_id"] is str
    assert processing_hints["return"] is ProcessingStatus
