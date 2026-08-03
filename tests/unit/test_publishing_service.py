from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from storehelper.artifacts.models import ArtifactInfo
from storehelper.config.models import ApplicationConfig
from storehelper.domain.errors import StoreHelperError
from storehelper.domain.models import PublishRequest, PublishStage
from storehelper.publishing.service import Publisher
from storehelper.runs.models import RunState
from storehelper.runs.repository import RunRepository
from storehelper.stores.errors import ArtifactStillProcessingError
from storehelper.stores.models import (
    ProcessingState,
    ProcessingStatus,
    ReviewStatus,
    UploadedArtifact,
    VerifiedApplication,
)

CompileState = ProcessingState
CompileStatus = ProcessingStatus


class FakeAdapter:
    def __init__(self, compile_states: list[CompileState] | None = None) -> None:
        self.compile_states = compile_states or [CompileState.READY]
        self.calls: list[str] = []
        self.submit_compiling_once = False

    async def verify(self, *, app_id: str, package_name: str) -> VerifiedApplication:
        self.calls.append("verify")
        return VerifiedApplication(app_id=app_id, package_name=package_name)

    async def upload(self, *, app_id: str, artifact: ArtifactInfo) -> UploadedArtifact:
        self.calls.append("upload")
        return UploadedArtifact(artifact_id="42")

    async def processing_status(
        self,
        *,
        app_id: str,
        artifact_id: str,
    ) -> ProcessingStatus:
        self.calls.append("compile")
        state = (
            self.compile_states.pop(0) if len(self.compile_states) > 1 else self.compile_states[0]
        )
        return CompileStatus(
            state=state, reason="compile failed" if state is CompileState.FAILED else None
        )

    async def update_release_notes(
        self,
        *,
        app_id: str,
        language: str,
        release_notes: str,
    ) -> None:
        self.calls.append("notes")

    async def submit(self, *, app_id: str) -> str:
        self.calls.append("submit")
        if self.submit_compiling_once:
            self.submit_compiling_once = False
            raise ArtifactStillProcessingError(
                "HUAWEI_PACKAGE_COMPILING",
                "Package is still compiling.",
                vendor_code="204144727",
            )
        return app_id

    async def review_status(self, *, app_id: str) -> ReviewStatus:
        self.calls.append("status")
        return ReviewStatus.IN_REVIEW


def _package(tmp_path: Path) -> Path:
    path = tmp_path / "release.apk"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
    return path


def _application(app_id: str = "123") -> ApplicationConfig:
    return ApplicationConfig.model_validate(
        {
            "package_name": "com.example.app",
            "stores": {
                "huawei": {
                    "app_id": app_id,
                    "credential_profile": "default",
                    "language": "zh-CN",
                }
            },
        }
    )


def _request(path: Path, **updates: object) -> PublishRequest:
    values: dict[str, object] = {
        "app_alias": "demo",
        "file": path,
        "release_notes": "Fixes",
        "submit": True,
        "confirmed": True,
        "poll_interval_seconds": 5,
        "wait_timeout_seconds": 30,
    }
    values.update(updates)
    return PublishRequest.model_validate(values)


@pytest.mark.asyncio
async def test_publish_uploads_waits_updates_and_submits(tmp_path: Path) -> None:
    adapter = FakeAdapter([CompileState.PROCESSING, CompileState.READY])
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    publisher = Publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
        application=_application(),
        sleeper=sleeper,
    )

    result = await publisher.publish(_request(_package(tmp_path)))

    assert result.ok is True
    assert result.stage is PublishStage.SUBMITTED
    assert adapter.calls == ["verify", "upload", "compile", "compile", "notes", "submit"]
    assert sleeps == [5]
    assert publisher.repository.get(result.run_id or "").state is RunState.COMPLETED


@pytest.mark.asyncio
async def test_dry_run_has_no_network_calls(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    publisher = Publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
        application=_application(),
    )

    result = await publisher.publish(_request(_package(tmp_path), dry_run=True))

    assert result.ok is True
    assert result.stage is PublishStage.COMPLETED
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_dry_run_is_not_blocked_by_existing_resumable_run(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "runs")
    package = _package(tmp_path)
    first_adapter = FakeAdapter([CompileState.PROCESSING])
    ticks = iter([0.0, 0.0, 5.0])

    async def sleeper(seconds: float) -> None:
        return None

    first = Publisher(
        adapter=first_adapter,
        repository=repo,
        application=_application(),
        clock=lambda: next(ticks),
        sleeper=sleeper,
    )
    timed_out = await first.publish(_request(package, wait_timeout_seconds=5))
    assert timed_out.resumable
    dry_adapter = FakeAdapter()
    dry = Publisher(adapter=dry_adapter, repository=repo, application=_application())

    result = await dry.publish(_request(package, dry_run=True))

    assert result.ok is True
    assert result.stage is PublishStage.COMPLETED
    assert dry_adapter.calls == []


@pytest.mark.asyncio
async def test_no_submit_stops_after_package_ready(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    publisher = Publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
        application=_application(),
    )

    result = await publisher.publish(
        _request(_package(tmp_path), submit=False, release_notes=None, confirmed=False)
    )

    assert result.ok is True
    assert adapter.calls == ["verify", "upload", "compile"]
    assert publisher.repository.get(result.run_id or "").state is RunState.COMPLETED


@pytest.mark.asyncio
async def test_timeout_is_resumable_without_reupload(tmp_path: Path) -> None:
    adapter = FakeAdapter([CompileState.PROCESSING])
    ticks = iter([0.0, 0.0, 5.0, 10.0])

    async def sleeper(seconds: float) -> None:
        return None

    publisher = Publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
        application=_application(),
        clock=lambda: next(ticks),
        sleeper=sleeper,
    )
    request = _request(_package(tmp_path), wait_timeout_seconds=5)

    result = await publisher.publish(request)

    assert result.ok is False
    assert result.resumable is True
    assert result.stage is PublishStage.TIMED_OUT
    assert result.next_action is not None
    assert "storehelper resume" in result.next_action.command
    assert publisher.repository.get(result.run_id or "").pkg_version == "42"


@pytest.mark.asyncio
async def test_duplicate_returns_existing_resume_action(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "runs")
    adapter = FakeAdapter([CompileState.PROCESSING])
    publisher = Publisher(adapter=adapter, repository=repo, application=_application())
    package = _package(tmp_path)
    first = await publisher.publish(
        _request(package, wait_timeout_seconds=5),
    )
    assert first.resumable
    adapter.calls.clear()

    second = await publisher.publish(_request(package))

    assert second.ok is False
    assert second.run_id == first.run_id
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_resume_from_timeout_starts_at_compile(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "runs")
    adapter = FakeAdapter([CompileState.PROCESSING])
    publisher = Publisher(adapter=adapter, repository=repo, application=_application())
    timed_out = await publisher.publish(
        _request(_package(tmp_path), wait_timeout_seconds=5),
    )
    adapter.calls.clear()
    adapter.compile_states = [CompileState.READY]

    resumed = await publisher.resume(timed_out.run_id or "")

    assert resumed.ok is True
    assert resumed.stage is PublishStage.SUBMITTED
    assert adapter.calls == ["compile", "notes", "submit"]


@pytest.mark.asyncio
async def test_resume_rejects_receipt_for_different_configured_app(tmp_path: Path) -> None:
    repo = RunRepository(tmp_path / "runs")
    receipt = repo.create(
        app_alias="demo",
        app_id="123",
        package_name="com.example.app",
        package_path="/build/release.apk",
        package_sha256="abc",
        logical_name="release.apk",
        language="zh-CN",
        release_notes="Fixes",
        submit=True,
    )
    publisher = Publisher(
        adapter=FakeAdapter(),
        repository=repo,
        application=_application(app_id="999"),
    )

    with pytest.raises(StoreHelperError) as raised:
        await publisher.resume(receipt.run_id)

    assert raised.value.code == "RUN_APP_MISMATCH"


@pytest.mark.asyncio
async def test_status_uses_normalized_adapter_result(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    publisher = Publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
        application=_application(),
    )

    result = await publisher.status()

    assert result.ok is True
    assert result.message.endswith("in_review")


@pytest.mark.asyncio
async def test_submit_compiling_rechecks_compile_state_then_retries(tmp_path: Path) -> None:
    adapter = FakeAdapter([CompileState.READY])
    adapter.submit_compiling_once = True
    publisher = Publisher(
        adapter=adapter,
        repository=RunRepository(tmp_path / "runs"),
        application=_application(),
    )

    result = await publisher.publish(_request(_package(tmp_path)))

    assert result.ok is True
    assert adapter.calls == [
        "verify",
        "upload",
        "compile",
        "notes",
        "submit",
        "compile",
        "submit",
    ]
