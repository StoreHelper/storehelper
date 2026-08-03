from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import storehelper.cli as cli_module
from storehelper.credentials.models import AppleApiKey, CredentialError, GoogleServiceAccount
from storehelper.credentials.providers import MemoryKeyring
from storehelper.domain.models import OperationResult, PublishStage
from storehelper.runs.repository import RunRepository
from storehelper.stores.models import CredentialKind, StoreName

runner = CliRunner()


def _project(tmp_path: Path, *, release_status: str = "draft") -> tuple[Path, Path]:
    config = tmp_path / "storehelper.yaml"
    config.write_text(
        f"""version: 1
apps:
  wallet:
    package_name: com.example.wallet
    stores:
      google_play:
        credential_profile: google-release
        track: internal
        release_status: {release_status}
        language: en-US
""",
        encoding="utf-8",
    )
    artifact = tmp_path / "wallet-release.aab"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("BundleConfig.pb", b"config")
        archive.writestr("base/manifest/AndroidManifest.xml", b"manifest")
        archive.writestr("assets/payload.bin", b"payload" * 128)
    return config, artifact


def _credential(private_key: str) -> GoogleServiceAccount:
    return GoogleServiceAccount(
        type="service_account",
        project_id="demo-project",
        private_key_id="google-key-1",
        private_key=private_key,
        client_email="storehelper@demo-project.iam.gserviceaccount.com",
    )


class GoogleBackend:
    def __init__(self, artifact: Path) -> None:
        self.sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
        self.requests: list[httpx.Request] = []
        self.track_body: dict[str, object] | None = None
        self.committed = False

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "google-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        path = request.url.path
        if path.endswith("/releases"):
            if not self.committed:
                return httpx.Response(200, json={})
            return httpx.Response(
                200,
                json={
                    "releases": [
                        {
                            "releaseName": "1.2.3",
                            "track": "internal",
                            "activeArtifacts": [{"versionCode": 42}],
                            "releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_PUBLISHED",
                        }
                    ]
                },
            )
        if path.endswith("/edits") and request.method == "POST":
            return httpx.Response(
                200,
                json={"id": "edit-123", "expiryTimeSeconds": "1786000000"},
            )
        if path.startswith("/upload/"):
            await request.aread()
            return httpx.Response(
                200,
                json={"versionCode": 42, "sha256": self.sha256},
            )
        if path.endswith("/edits/edit-123"):
            return httpx.Response(
                200,
                json={"id": "edit-123", "expiryTimeSeconds": "1786000000"},
            )
        if path.endswith("/tracks/internal") and request.method == "GET":
            return httpx.Response(200, json={"track": "internal", "releases": []})
        if path.endswith("/tracks/internal") and request.method == "PUT":
            self.track_body = json.loads(await request.aread())
            return httpx.Response(200, json=self.track_body)
        if path.endswith(":validate"):
            return httpx.Response(
                200,
                json={"id": "edit-123", "expiryTimeSeconds": "1786000000"},
            )
        if path.endswith(":commit"):
            self.committed = True
            return httpx.Response(
                200,
                json={"id": "edit-123", "expiryTimeSeconds": "1786000000"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


def _install_runtime(
    *,
    tmp_path: Path,
    artifact: Path,
    private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> GoogleBackend:
    keyring = MemoryKeyring()
    keyring.set(
        "google-release",
        _credential(private_key).to_storage_json(),
        CredentialKind.GOOGLE_SERVICE_ACCOUNT,
    )
    backend = GoogleBackend(artifact)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(cli_module, "KEYRING", keyring)
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(backend),
            **kwargs,
        ),
    )
    return backend


def test_publish_help_lists_google_play() -> None:
    result = runner.invoke(cli_module.app, ["publish", "--help"])

    assert result.exit_code == 0
    assert "google_play" in result.stdout


def test_google_dry_run_needs_no_credentials_or_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, artifact = _project(tmp_path)
    monkeypatch.setattr(cli_module, "KEYRING", MemoryKeyring())
    monkeypatch.setattr(cli_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(
        cli_module.httpx,
        "AsyncClient",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("dry-run created HTTP client")),
    )

    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--app",
            "wallet",
            "--store",
            "google_play",
            "--file",
            str(artifact),
            "--dry-run",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["store"] == "google_play"
    assert payload["stage"] == "completed"


@pytest.mark.parametrize(
    ("release_status", "extra", "expected_stage"),
    [
        ("draft", ["--no-submit"], "package_ready"),
        ("draft", ["--yes", "--release-notes", "Draft notes"], "submitted"),
        ("completed", ["--yes"], "submitted"),
    ],
)
def test_google_real_cli_no_submit_draft_and_completed_flows(
    tmp_path: Path,
    rsa_private_key: str,
    monkeypatch: pytest.MonkeyPatch,
    release_status: str,
    extra: list[str],
    expected_stage: str,
) -> None:
    config, artifact = _project(tmp_path, release_status=release_status)
    backend = _install_runtime(
        tmp_path=tmp_path,
        artifact=artifact,
        private_key=rsa_private_key,
        monkeypatch=monkeypatch,
    )

    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--app",
            "wallet",
            "--store",
            "google_play",
            "--file",
            str(artifact),
            "--output",
            "json",
            "--config",
            str(config),
            *extra,
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["stage"] == expected_stage
    if expected_stage == "package_ready":
        assert backend.track_body is None
        receipt = RunRepository(tmp_path / "runs").list()[0]
        assert receipt.operation_id == "edit-123"
        assert receipt.artifact_id == "42"
    else:
        assert backend.track_body is not None
        release = backend.track_body["releases"][-1]
        assert release["status"] == release_status
        assert backend.committed is True


def test_google_noninteractive_submit_requires_confirmation(tmp_path: Path) -> None:
    config, artifact = _project(tmp_path)

    result = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--app",
            "wallet",
            "--store",
            "google_play",
            "--file",
            str(artifact),
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 2
    assert "--yes" in result.stdout


def test_google_credentials_verify_and_status_use_read_only_lifecycle(
    tmp_path: Path,
    rsa_private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, artifact = _project(tmp_path)
    backend = _install_runtime(
        tmp_path=tmp_path,
        artifact=artifact,
        private_key=rsa_private_key,
        monkeypatch=monkeypatch,
    )
    verified = runner.invoke(
        cli_module.app,
        [
            "credentials",
            "verify",
            "--app",
            "wallet",
            "--store",
            "google_play",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )
    backend.committed = True
    status = runner.invoke(
        cli_module.app,
        [
            "status",
            "--app",
            "wallet",
            "--store",
            "google_play",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )

    assert verified.exit_code == 0, verified.stdout
    assert json.loads(verified.stdout)["stage"] == "app_verified"
    assert status.exit_code == 0, status.stdout
    assert json.loads(status.stdout)["message"].endswith("approved")
    api_requests = [
        request for request in backend.requests if request.url.host != "oauth2.googleapis.com"
    ]
    assert all(request.method == "GET" for request in api_requests)
    assert all("/edits" not in request.url.path for request in api_requests)


@pytest.mark.asyncio
async def test_google_wrong_credential_kind_is_rejected_before_network(
    tmp_path: Path,
    p256_private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _ = _project(tmp_path)
    wrong = AppleApiKey(
        key_type="team",
        key_id="APPLEKEY1",
        issuer_id="issuer-1",
        private_key=p256_private_key,
    )
    keyring = MemoryKeyring()
    keyring.set(
        "google-release",
        wrong.to_storage_json(),
        CredentialKind.GOOGLE_SERVICE_ACCOUNT,
    )
    monkeypatch.setattr(cli_module, "KEYRING", keyring)

    with pytest.raises(CredentialError) as raised:
        await cli_module._status_operation(
            config_path=config,
            app_alias="wallet",
            store=StoreName.GOOGLE_PLAY,
            interactive=False,
        )

    assert raised.value.code == "CREDENTIAL_KIND_MISMATCH"


def test_google_cli_timeout_and_resume_output_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, artifact = _project(tmp_path)

    async def fake_publish(**kwargs) -> OperationResult:
        return OperationResult.failure(
            store=StoreName.GOOGLE_PLAY,
            stage=PublishStage.TIMED_OUT,
            run_id="google-run",
            message="Commit outcome is being reconciled.",
            resumable=True,
        )

    async def fake_resume(**kwargs) -> OperationResult:
        return OperationResult.success(
            store=StoreName.GOOGLE_PLAY,
            stage=PublishStage.SUBMITTED,
            run_id=kwargs["run_id"],
        )

    monkeypatch.setattr(cli_module, "_publish_operation", fake_publish)
    timed_out = runner.invoke(
        cli_module.app,
        [
            "publish",
            "--app",
            "wallet",
            "--store",
            "google_play",
            "--file",
            str(artifact),
            "--yes",
            "--output",
            "json",
            "--config",
            str(config),
        ],
    )
    monkeypatch.setattr(cli_module, "_resume_operation", fake_resume)
    resumed = runner.invoke(
        cli_module.app,
        ["resume", "google-run", "--output", "json", "--config", str(config)],
    )

    assert timed_out.exit_code == 6
    assert json.loads(timed_out.stdout)["next_action"]["command"] == (
        "storehelper resume google-run"
    )
    assert resumed.exit_code == 0
    assert json.loads(resumed.stdout)["stage"] == "submitted"
