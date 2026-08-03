from __future__ import annotations

import json
import plistlib
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import storehelper.cli as cli_module
from storehelper.credentials.models import AppleApiKey, CredentialError, HuaweiServiceAccount
from storehelper.credentials.providers import MemoryKeyring
from storehelper.domain.models import OperationResult, PublishStage
from storehelper.runs.models import RunReceipt, RunState
from storehelper.runs.repository import RunRepository
from storehelper.stores.models import CredentialKind, StoreName

runner = CliRunner()


def _project(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "storehelper.yaml"
    config.write_text(
        """version: 1
apps:
  wallet:
    package_name: com.example.wallet
    stores:
      apple:
        app_id: app-123
        bundle_id: com.example.wallet.ios
        app_store_version_id: version-456
        credential_profile: apple-release
        platform: IOS
        language: zh-Hans
""",
        encoding="utf-8",
    )
    ipa = tmp_path / "Wallet.ipa"
    with zipfile.ZipFile(ipa, "w") as archive:
        archive.writestr(
            "Payload/Wallet.app/Info.plist",
            plistlib.dumps(
                {
                    "CFBundleIdentifier": "com.example.wallet.ios",
                    "CFBundleShortVersionString": "1.2.3",
                    "CFBundleVersion": "42",
                }
            ),
        )
        archive.writestr("Payload/Wallet.app/Wallet", b"binary")
    return config, ipa


def _credential(private_key: str) -> AppleApiKey:
    return AppleApiKey(
        key_type="team",
        key_id="APPLEKEY1",
        issuer_id="issuer-1",
        private_key=private_key,
    )


def _apple_transport(
    ipa_size: int,
    requests: list[httpx.Request],
) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if request.url.host == "uploads.example":
            assert "authorization" not in request.headers
            await request.aread()
            return httpx.Response(200)
        if path == "/v1/apps/app-123" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "type": "apps",
                        "id": "app-123",
                        "attributes": {
                            "bundleId": "com.example.wallet.ios",
                            "name": "Wallet",
                        },
                    }
                },
            )
        if path == "/v1/appStoreVersions/version-456" and "include" in request.url.params:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "type": "appStoreVersions",
                        "id": "version-456",
                        "attributes": {
                            "platform": "IOS",
                            "versionString": "1.2.3",
                            "appStoreState": "READY_FOR_REVIEW",
                        },
                        "relationships": {"app": {"data": {"type": "apps", "id": "app-123"}}},
                    }
                },
            )
        if path == "/v1/appStoreVersions/version-456" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "type": "appStoreVersions",
                        "id": "version-456",
                        "attributes": {"appStoreState": "IN_REVIEW"},
                    }
                },
            )
        if path == "/v1/apps/app-123/buildUploads":
            return httpx.Response(200, json={"data": []})
        if path == "/v1/buildUploads" and request.method == "POST":
            return httpx.Response(
                201,
                json={"data": {"type": "buildUploads", "id": "upload-456"}},
            )
        if path == "/v1/buildUploadFiles" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "data": {
                        "type": "buildUploadFiles",
                        "id": "file-789",
                        "attributes": {
                            "assetType": "ASSET",
                            "fileName": "Wallet.ipa",
                            "fileSize": ipa_size,
                            "uti": "com.apple.ipa",
                            "uploadOperations": [
                                {
                                    "method": "PUT",
                                    "url": "https://uploads.example/file?signature=private",
                                    "offset": 0,
                                    "length": ipa_size,
                                    "partNumber": 1,
                                    "expiration": "2099-08-03T09:00:00Z",
                                    "requestHeaders": [
                                        {
                                            "name": "Content-Type",
                                            "value": "application/octet-stream",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                },
            )
        if path == "/v1/buildUploadFiles/file-789" and request.method == "PATCH":
            return httpx.Response(
                200,
                json={"data": {"type": "buildUploadFiles", "id": "file-789"}},
            )
        if path == "/v1/buildUploads/upload-456":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "type": "buildUploads",
                        "id": "upload-456",
                        "attributes": {"state": {"state": "COMPLETE"}},
                        "relationships": {"build": {"data": {"type": "builds", "id": "build-999"}}},
                    }
                },
            )
        if path == "/v1/appStoreVersions/version-456/relationships/build":
            return httpx.Response(204)
        if path == "/v1/apps/app-123/reviewSubmissions":
            return httpx.Response(200, json={"data": []})
        if path == "/v1/reviewSubmissions" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "data": {
                        "type": "reviewSubmissions",
                        "id": "submission-1",
                        "attributes": {"platform": "IOS", "state": "READY_FOR_REVIEW"},
                    }
                },
            )
        if path == "/v1/reviewSubmissions/submission-1/items":
            return httpx.Response(200, json={"data": []})
        if path == "/v1/reviewSubmissionItems" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "data": {
                        "type": "reviewSubmissionItems",
                        "id": "item-1",
                        "relationships": {
                            "appStoreVersion": {
                                "data": {"type": "appStoreVersions", "id": "version-456"}
                            }
                        },
                    }
                },
            )
        if path == "/v1/reviewSubmissions/submission-1" and request.method == "PATCH":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "type": "reviewSubmissions",
                        "id": "submission-1",
                        "attributes": {"platform": "IOS", "state": "WAITING_FOR_REVIEW"},
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return httpx.MockTransport(handler)


def _install_apple_runtime(
    *,
    tmp_path: Path,
    ipa: Path,
    private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> list[httpx.Request]:
    keyring = MemoryKeyring()
    keyring.set(
        "apple-release",
        _credential(private_key).to_storage_json(),
        CredentialKind.APPLE_API_KEY,
    )
    monkeypatch.setattr(cli_module, "KEYRING", keyring)
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")
    real_async_client = httpx.AsyncClient
    requests: list[httpx.Request] = []
    transport = _apple_transport(ipa.stat().st_size, requests)
    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=transport),
    )
    return requests


def test_apple_dry_run_needs_no_credentials_or_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, ipa = _project(tmp_path)
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")

    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--app",
            "wallet",
            "--store",
            "apple",
            "--file",
            str(ipa),
            "--dry-run",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["store"] == "apple"
    assert payload["stage"] == "completed"


def test_apple_full_submit_keeps_release_notes_optional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, ipa = _project(tmp_path)

    async def fake_publish(**kwargs) -> OperationResult:
        request = kwargs["request"]
        assert request.store is StoreName.APPLE
        assert request.submit is True
        assert request.release_notes is None
        return OperationResult.success(
            store=StoreName.APPLE,
            stage=PublishStage.SUBMITTED,
            run_id="apple-run",
        )

    monkeypatch.setattr(cli_module, "_publish_operation", fake_publish)
    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--app",
            "wallet",
            "--store",
            "apple",
            "--file",
            str(ipa),
            "--yes",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["stage"] == "submitted"


@pytest.mark.parametrize(
    ("extra", "expected_stage"),
    [(["--no-submit"], "package_ready"), ([], "submitted")],
)
def test_apple_real_cli_no_submit_and_full_json_flow(
    tmp_path: Path,
    p256_private_key: str,
    monkeypatch: pytest.MonkeyPatch,
    extra: list[str],
    expected_stage: str,
) -> None:
    config, ipa = _project(tmp_path)
    requests = _install_apple_runtime(
        tmp_path=tmp_path,
        ipa=ipa,
        private_key=p256_private_key,
        monkeypatch=monkeypatch,
    )
    arguments = [
        "publish",
        "--app",
        "wallet",
        "--store",
        "apple",
        "--file",
        str(ipa),
        "--yes",
        "--output",
        "json",
        "--config",
        str(config),
        *extra,
    ]

    result = runner.invoke(cli_module.app, arguments)

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["stage"] == expected_stage
    submitted = any(
        request.url.path == "/v1/reviewSubmissions/submission-1" for request in requests
    )
    assert submitted is (expected_stage == "submitted")


def test_apple_status_uses_apple_runtime(
    tmp_path: Path,
    p256_private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, ipa = _project(tmp_path)
    _install_apple_runtime(
        tmp_path=tmp_path,
        ipa=ipa,
        private_key=p256_private_key,
        monkeypatch=monkeypatch,
    )

    result = runner.invoke(
        cli_module.app,
        [
            "status",
            "--app",
            "wallet",
            "--store",
            "apple",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["store"] == "apple"
    assert payload["message"].endswith("in_review")


def test_apple_timeout_preserves_resume_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, ipa = _project(tmp_path)

    async def fake_publish(**kwargs) -> OperationResult:
        assert kwargs["request"].store is StoreName.APPLE
        return OperationResult.failure(
            store=StoreName.APPLE,
            stage=PublishStage.TIMED_OUT,
            run_id="apple-run",
            message="Apple is still processing the build.",
            resumable=True,
        )

    monkeypatch.setattr(cli_module, "_publish_operation", fake_publish)
    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--app",
            "wallet",
            "--store",
            "apple",
            "--file",
            str(ipa),
            "--no-submit",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 6
    payload = json.loads(result.stdout)
    assert payload["next_action"]["command"] == "storehelper resume apple-run"


@pytest.mark.asyncio
async def test_apple_rejects_wrong_credential_kind_before_network(
    tmp_path: Path,
    rsa_private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _ = _project(tmp_path)
    wrong = HuaweiServiceAccount(
        key_id="huawei-key",
        sub_account="sub-account",
        private_key=rsa_private_key,
    )
    keyring = MemoryKeyring()
    keyring.set(
        "apple-release",
        wrong.to_storage_json(),
        CredentialKind.APPLE_API_KEY,
    )
    monkeypatch.setattr(cli_module, "KEYRING", keyring)

    with pytest.raises(CredentialError) as raised:
        await cli_module._status_operation(
            config_path=config,
            app_alias="wallet",
            store=StoreName.APPLE,
            interactive=False,
        )

    assert raised.value.code == "CREDENTIAL_KIND_MISMATCH"


@pytest.mark.asyncio
async def test_apple_verify_resolves_apple_credential_namespace(
    tmp_path: Path,
    p256_private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _ = _project(tmp_path)
    keyring = MemoryKeyring()
    keyring.set(
        "apple-release",
        _credential(p256_private_key).to_storage_json(),
        CredentialKind.APPLE_API_KEY,
    )
    monkeypatch.setattr(cli_module, "KEYRING", keyring)
    real_async_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/apps/app-123":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "type": "apps",
                        "id": "app-123",
                        "attributes": {
                            "bundleId": "com.example.wallet.ios",
                            "name": "Wallet",
                        },
                    }
                },
            )
        if request.url.path == "/v1/appStoreVersions/version-456":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "type": "appStoreVersions",
                        "id": "version-456",
                        "attributes": {
                            "platform": "IOS",
                            "versionString": "1.2.3",
                            "appStoreState": "READY_FOR_REVIEW",
                        },
                        "relationships": {"app": {"data": {"type": "apps", "id": "app-123"}}},
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(handler)),
    )

    result = await cli_module._verify_credentials_operation(
        config_path=config,
        app_alias="wallet",
        profile=None,
        store=StoreName.APPLE,
        interactive=False,
    )

    assert result.store is StoreName.APPLE
    assert result.stage is PublishStage.APP_VERIFIED


@pytest.mark.asyncio
async def test_resume_derives_apple_credential_kind_from_receipt(
    tmp_path: Path,
    p256_private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, ipa = _project(tmp_path)
    repository = RunRepository(tmp_path / "runs")
    now = datetime.now(UTC)
    repository.save(
        RunReceipt(
            run_id="apple-run",
            created_at=now,
            updated_at=now,
            store=StoreName.APPLE,
            state=RunState.PACKAGE_COMPILING,
            app_alias="wallet",
            app_id="app-123",
            package_name="com.example.wallet.ios",
            package_path=str(ipa),
            package_sha256="not-used-by-fake-publisher",
            logical_name="Wallet.ipa",
            artifact_id="upload-123",
            release_id="version-456",
            language="zh-Hans",
        )
    )
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")
    resolved: list[CredentialKind] = []
    credential = _credential(p256_private_key)

    class RecordingProvider:
        def __init__(self, keyring) -> None:
            pass

        def resolve(self, profile, kind=CredentialKind.HUAWEI_SERVICE_ACCOUNT, *, interactive):
            resolved.append(kind)
            return credential

    class FakePublisher:
        async def resume(self, run_id, *, poll_interval, wait_timeout):
            return OperationResult.success(
                store=StoreName.APPLE,
                stage=PublishStage.PACKAGE_READY,
                run_id=run_id,
            )

    monkeypatch.setattr(cli_module, "CredentialProvider", RecordingProvider)
    monkeypatch.setattr(cli_module, "build_runtime", lambda *args: object())
    monkeypatch.setattr(cli_module, "_publisher", lambda **kwargs: FakePublisher())

    result = await cli_module._resume_operation(
        run_id="apple-run",
        config_path=config,
        app_alias=None,
        interactive=False,
        poll_interval=5,
        wait_timeout=5,
    )

    assert result.store is StoreName.APPLE
    assert resolved == [CredentialKind.APPLE_API_KEY]


def test_apple_submit_interactive_confirmation_does_not_prompt_for_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, ipa = _project(tmp_path)

    async def fake_publish(**kwargs) -> OperationResult:
        assert kwargs["request"].release_notes is None
        return OperationResult.success(
            store=StoreName.APPLE,
            stage=PublishStage.SUBMITTED,
            run_id="apple-run",
        )

    monkeypatch.setattr(cli_module, "_interactive", lambda output: True)
    monkeypatch.setattr(cli_module, "_publish_operation", fake_publish)
    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--app",
            "wallet",
            "--store",
            "apple",
            "--file",
            str(ipa),
            "--config",
            str(config),
        ],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "Release notes" not in result.stdout
