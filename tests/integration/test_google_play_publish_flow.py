from __future__ import annotations

import json
import zipfile
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from storehelper.credentials.models import GoogleServiceAccount
from storehelper.domain.errors import StoreHelperError
from storehelper.domain.models import PublishRequest, PublishStage
from storehelper.output.renderers import render_result
from storehelper.publishing.service import Publisher
from storehelper.runs.models import RunState
from storehelper.runs.repository import RunRepository
from storehelper.stores.google_play.adapter import GooglePlayAdapter
from storehelper.stores.google_play.auth import GoogleAuth
from storehelper.stores.google_play.client import GooglePlayClient
from storehelper.stores.google_play.errors import GoogleVendorError
from storehelper.stores.google_play.package import validate_google_play_artifact
from storehelper.stores.models import (
    CredentialKind,
    StoreCapabilities,
    StoreName,
    StoreTarget,
)


async def _no_sleep(seconds: float) -> None:
    del seconds


def _credential(private_key: str) -> GoogleServiceAccount:
    return GoogleServiceAccount.model_validate(
        {
            "type": "service_account",
            "project_id": "storehelper-test",
            "private_key_id": "fake-google-key",
            "private_key": private_key,
            "client_email": "publisher@storehelper-test.iam.gserviceaccount.com",
        }
    )


def _artifact(tmp_path: Path, suffix: str = ".aab") -> Path:
    path = tmp_path / f"wallet-release{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        if suffix == ".aab":
            archive.writestr("BundleConfig.pb", b"config")
            archive.writestr("base/manifest/AndroidManifest.xml", b"manifest")
        else:
            archive.writestr("AndroidManifest.xml", b"manifest")
            archive.writestr("META-INF/CERT.RSA", b"signature")
        archive.writestr("assets/payload.bin", b"storehelper" * 128)
    return path


def _target(*, track: str = "internal", status: str = "draft") -> StoreTarget:
    return StoreTarget(
        store=StoreName.GOOGLE_PLAY,
        label=f"Google Play ({track}, {status})",
        app_id="com.example.wallet",
        package_name="com.example.wallet",
        credential_profile="google-release",
        language="en-US",
        track=track,
        release_status=status,
    )


def _request(path: Path, **updates: object) -> PublishRequest:
    values: dict[str, object] = {
        "app_alias": "wallet",
        "store": StoreName.GOOGLE_PLAY,
        "file": path,
        "release_notes": "Safer Google Play release",
        "submit": True,
        "confirmed": True,
        "poll_interval_seconds": 5,
        "wait_timeout_seconds": 5,
    }
    values.update(updates)
    return PublishRequest.model_validate(values)


class GooglePlayBackend:
    """Stateful Google API double used through the real HTTP/auth/client stack."""

    def __init__(self, artifact: Path) -> None:
        self.artifact = validate_google_play_artifact(artifact)
        self.edit_id = "edit-123"
        self.version_code = 42
        self.visible = False
        self.expired = False
        self.lose_track_response_once = False
        self.lose_commit_response_once = False
        self.apply_lost_commit = False
        self.reject_track_with_secret = False
        self.requests = 0
        self.uploads = 0
        self.track_updates = 0
        self.commits = 0
        self.track_releases: list[dict[str, object]] = []
        self.assertion: str | None = None

    def _edit(self) -> dict[str, str]:
        return {"id": self.edit_id, "expiryTimeSeconds": "1893456000"}

    def _lifecycle(self) -> dict[str, object]:
        artifacts = [{"versionCode": self.version_code}] if self.visible else []
        return {
            "releases": [
                {
                    "releaseName": "1.2.3",
                    "track": "internal",
                    "activeArtifacts": artifacts,
                    "releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_DRAFT",
                }
            ]
        }

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs((await request.aread()).decode("ascii"))
            self.assertion = form["assertion"][0]
            return httpx.Response(
                200,
                json={
                    "access_token": "known-google-access-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )

        path = request.url.path
        if path.endswith("/releases") and "/edits/" not in path:
            return httpx.Response(200, json=self._lifecycle())
        if request.method == "POST" and path.endswith("/edits"):
            return httpx.Response(200, json=self._edit())
        if path.startswith("/upload/androidpublisher/"):
            self.uploads += 1
            assert await request.aread() == self.artifact.path.read_bytes()
            payload: dict[str, object] = {
                "versionCode": self.version_code,
                "sha256": self.artifact.sha256,
            }
            if path.endswith("/apks"):
                payload = {
                    "versionCode": self.version_code,
                    "binary": {"sha256": self.artifact.sha256},
                }
            return httpx.Response(200, json=payload)
        if request.method == "GET" and path.endswith(f"/edits/{self.edit_id}"):
            if self.expired:
                return httpx.Response(
                    404,
                    json={"error": {"status": "NOT_FOUND", "message": "expired edit"}},
                )
            return httpx.Response(200, json=self._edit())
        if "/tracks/" in path and request.method == "GET":
            return httpx.Response(
                200,
                json={"track": path.rsplit("/", 1)[-1], "releases": self.track_releases},
            )
        if "/tracks/" in path and request.method == "PUT":
            self.track_updates += 1
            if self.reject_track_with_secret:
                return httpx.Response(
                    503,
                    json={
                        "error": {
                            "status": "UNAVAILABLE",
                            "message": "private_key=known-secret-marker",
                        }
                    },
                )
            payload = json.loads(await request.aread())
            self.track_releases = payload["releases"]
            if self.lose_track_response_once:
                self.lose_track_response_once = False
                raise httpx.ReadError(
                    "access_token=known-google-access-token",
                    request=request,
                )
            return httpx.Response(200, json=payload)
        if path.endswith(":validate"):
            if self.expired:
                return httpx.Response(404, json={"error": {"status": "NOT_FOUND"}})
            return httpx.Response(200, json=self._edit())
        if path.endswith(":commit"):
            self.commits += 1
            if self.lose_commit_response_once:
                self.lose_commit_response_once = False
                if self.apply_lost_commit:
                    self.visible = True
                raise httpx.ReadError("lost commit response", request=request)
            self.visible = True
            return httpx.Response(200, json=self._edit())
        raise AssertionError(f"unexpected Google request: {request.method} {request.url}")


def _publisher(
    tmp_path: Path,
    rsa_private_key: str,
    backend: GooglePlayBackend,
    *,
    track: str = "internal",
    status: str = "draft",
) -> tuple[Publisher, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    auth = GoogleAuth(_credential(rsa_private_key), http)
    client = GooglePlayClient(auth=auth, http=http, sleeper=_no_sleep)
    publisher = Publisher(
        adapter=GooglePlayAdapter(client),
        repository=RunRepository(tmp_path / "runs"),
        target=_target(track=track, status=status),
        validator=validate_google_play_artifact,
        capabilities=StoreCapabilities(
            credential_kind=CredentialKind.GOOGLE_SERVICE_ACCOUNT,
            artifact_suffixes=(".aab", ".apk"),
            requires_processing_poll=False,
            requires_release_notes=False,
            supports_review_status=True,
        ),
        sleeper=_no_sleep,
    )
    return publisher, http


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "status"),
    [(".aab", "draft"), (".aab", "completed"), (".apk", "draft")],
)
async def test_full_aab_or_apk_publish_commits_exact_release(
    tmp_path: Path,
    rsa_private_key: str,
    suffix: str,
    status: str,
) -> None:
    package = _artifact(tmp_path, suffix)
    backend = GooglePlayBackend(package)
    publisher, http = _publisher(tmp_path, rsa_private_key, backend, status=status)

    async with http:
        result = await publisher.publish(_request(package))

    assert result.stage is PublishStage.SUBMITTED
    receipt = publisher.repository.get(result.run_id or "")
    assert receipt.state is RunState.COMPLETED
    assert receipt.artifact_id == "42"
    assert receipt.operation_id == "edit-123"
    assert receipt.submission_id == "42"
    assert backend.uploads == backend.track_updates == backend.commits == 1
    assert backend.track_releases[-1]["status"] == status


@pytest.mark.asyncio
async def test_no_submit_stops_before_track_update_and_dry_run_has_zero_http(
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    package = _artifact(tmp_path)
    backend = GooglePlayBackend(package)
    publisher, http = _publisher(tmp_path / "no-submit", rsa_private_key, backend)
    async with http:
        result = await publisher.publish(
            _request(package, submit=False, confirmed=False, release_notes=None)
        )

    assert result.stage is PublishStage.PACKAGE_READY
    assert backend.uploads == 1
    assert backend.track_updates == backend.commits == 0

    dry_backend = GooglePlayBackend(package)
    dry, dry_http = _publisher(tmp_path / "dry-run", rsa_private_key, dry_backend)
    async with dry_http:
        result = await dry.publish(
            _request(
                package,
                submit=False,
                confirmed=False,
                release_notes=None,
                dry_run=True,
            )
        )

    assert result.stage is PublishStage.COMPLETED
    assert dry_backend.requests == 0


@pytest.mark.asyncio
async def test_lost_track_response_resumes_idempotently_without_reupload(
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    package = _artifact(tmp_path)
    backend = GooglePlayBackend(package)
    backend.lose_track_response_once = True
    publisher, http = _publisher(tmp_path, rsa_private_key, backend)

    async with http:
        interrupted = await publisher.publish(_request(package))
        assert interrupted.resumable is True
        assert publisher.repository.get(interrupted.run_id or "").state is RunState.PACKAGE_READY
        resumed = await publisher.resume(interrupted.run_id or "")

    assert resumed.stage is PublishStage.SUBMITTED
    assert backend.uploads == 1
    assert backend.track_updates == 1
    assert backend.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("visible_after_loss", [True, False])
async def test_lost_commit_response_reconciles_or_resumes_without_reupload(
    tmp_path: Path,
    rsa_private_key: str,
    visible_after_loss: bool,
) -> None:
    package = _artifact(tmp_path)
    backend = GooglePlayBackend(package)
    backend.lose_commit_response_once = True
    backend.apply_lost_commit = visible_after_loss
    publisher, http = _publisher(tmp_path, rsa_private_key, backend)

    async with http:
        result = await publisher.publish(_request(package))
        if not visible_after_loss:
            assert result.resumable is True
            assert publisher.repository.get(result.run_id or "").state is RunState.METADATA_UPDATED
            backend.visible = True
            result = await publisher.resume(result.run_id or "")

    assert result.stage is PublishStage.SUBMITTED
    assert backend.uploads == backend.track_updates == backend.commits == 1


@pytest.mark.asyncio
async def test_expired_edit_fails_with_actionable_new_publish_instruction(
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    package = _artifact(tmp_path)
    backend = GooglePlayBackend(package)
    backend.expired = True
    publisher, http = _publisher(tmp_path, rsa_private_key, backend)

    async with http:
        with pytest.raises(GoogleVendorError) as raised:
            await publisher.publish(_request(package))

    assert raised.value.code == "GOOGLE_EDIT_EXPIRED"
    assert raised.value.resumable is False
    assert "start a new publish" in raised.value.message
    assert backend.uploads == 1
    assert backend.track_updates == backend.commits == 0


@pytest.mark.asyncio
async def test_changed_artifact_or_target_is_rejected_before_resume_http(
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    package = _artifact(tmp_path)
    backend = GooglePlayBackend(package)
    backend.lose_track_response_once = True
    publisher, http = _publisher(tmp_path, rsa_private_key, backend)
    async with http:
        interrupted = await publisher.publish(_request(package))
        before = backend.requests
        with zipfile.ZipFile(package, "a") as archive:
            archive.writestr("assets/changed", b"changed")
        with pytest.raises(StoreHelperError) as changed:
            await publisher.resume(interrupted.run_id or "")
        assert changed.value.code == "PACKAGE_CHANGED"
        assert backend.requests == before

    original = _artifact(tmp_path / "target")
    target_backend = GooglePlayBackend(original)
    target_backend.lose_track_response_once = True
    first, first_http = _publisher(tmp_path / "target", rsa_private_key, target_backend)
    async with first_http:
        interrupted = await first.publish(_request(original))
    changed_target, changed_http = _publisher(
        tmp_path / "target",
        rsa_private_key,
        target_backend,
        track="production",
        status="completed",
    )
    before = target_backend.requests
    async with changed_http:
        with pytest.raises(StoreHelperError) as mismatch:
            await changed_target.resume(interrupted.run_id or "")
    assert mismatch.value.code == "RUN_APP_MISMATCH"
    assert target_backend.requests == before


@pytest.mark.asyncio
async def test_google_secrets_and_raw_errors_never_enter_output_or_receipt(
    tmp_path: Path,
    rsa_private_key: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = _artifact(tmp_path)
    backend = GooglePlayBackend(package)
    backend.reject_track_with_secret = True
    publisher, http = _publisher(tmp_path, rsa_private_key, backend)

    async with http:
        result = await publisher.publish(_request(package))

    render_result(result, output="json")
    output = capsys.readouterr().out
    receipt = (publisher.repository.root / f"{result.run_id}.json").read_text(encoding="utf-8")
    forbidden = (
        rsa_private_key,
        backend.assertion or "missing-assertion",
        "known-google-access-token",
        "known-secret-marker",
    )
    for secret in forbidden:
        assert secret not in output
        assert secret not in receipt
    assert result.resumable is True
