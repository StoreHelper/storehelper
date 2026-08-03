from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from storehelper.credentials.models import HuaweiServiceAccount
from storehelper.domain.models import OperationResult, PublishRequest, PublishStage
from storehelper.publishing.service import Publisher
from storehelper.runs.models import RunState
from storehelper.runs.repository import RunRepository
from storehelper.stores.harmonyos.package import validate_harmonyos_artifact
from storehelper.stores.models import StoreName, StoreTarget
from storehelper.stores.registry import get_registration


class HarmonyBackend:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.processing_states = ["ready"]
        self.processing_calls = 0
        self.upload_calls = 0
        self.submit_calls = 0
        self.submit_compiling_once = False
        self.cancel_processing_once = False
        self.obs_authorization = "AWS4-HMAC-SHA256 Credential=temporary-upload-secret"
        self.object_id = "CN/20260803/private-object.app"
        self.obs_url = "https://obs.example/private/wallet.app"

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        if path.endswith("/api/publish/v2/appid-list"):
            return httpx.Response(
                200,
                json={"ret": {"code": 0}, "appids": [{"value": "100000002"}]},
            )
        if path.endswith("/api/publish/v2/upload-url/for-obs"):
            content_length = request.url.params["contentLength"]
            return httpx.Response(
                200,
                json={
                    "ret": {"code": 0},
                    "urlInfo": {
                        "objectId": self.object_id,
                        "url": self.obs_url,
                        "method": "PUT",
                        "headers": {
                            "Authorization": self.obs_authorization,
                            "Content-Type": "application/octet-stream",
                            "Content-Length": content_length,
                        },
                    },
                },
            )
        if request.url.host == "obs.example":
            self.upload_calls += 1
            assert request.headers["authorization"] == self.obs_authorization
            assert "client_id" not in request.headers
            assert await request.aread()
            return httpx.Response(200)
        if path.endswith("/api/publish/v3/app-package-info"):
            payload = json.loads(await request.aread())
            assert payload == {
                "fileName": request.url.params.get("fileName", payload["fileName"]),
                "objectId": self.object_id,
            }
            return httpx.Response(
                200,
                json={"ret": {"code": 0}, "packageId": "package-42"},
            )
        if path.endswith("/api/publish/v2/app-package-info"):
            self.processing_calls += 1
            if self.cancel_processing_once:
                self.cancel_processing_once = False
                raise asyncio.CancelledError
            state = (
                self.processing_states.pop(0)
                if len(self.processing_states) > 1
                else self.processing_states[0]
            )
            return httpx.Response(
                200,
                json={"ret": {"code": 0}, "packageInfo": {"parseStatus": state}},
            )
        if path.endswith("/api/publish/v3/app-language-info"):
            payload = json.loads(await request.aread())
            assert set(payload) == {"lang", "newFeatures"}
            return httpx.Response(200, json={"ret": {"code": 0}})
        if path.endswith("/api/publish/v3/app-submit"):
            self.submit_calls += 1
            if self.submit_compiling_once and self.submit_calls == 1:
                return httpx.Response(
                    200,
                    json={
                        "ret": {
                            "code": 204144727,
                            "msg": "package is being compiled",
                        }
                    },
                )
            return httpx.Response(200, json={"ret": {"code": 0}})
        raise AssertionError(f"unexpected HarmonyOS request: {request.method} {path}")


class TickClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        current = self.value
        self.value += 5.0
        return current


def _artifact(tmp_path: Path, suffix: str = ".app") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"wallet{suffix}"
    with zipfile.ZipFile(path, "w") as archive:
        if suffix == ".app":
            archive.writestr("pack.info", b"{}")
            archive.writestr("entry.hap", b"signed-hap")
        else:
            archive.writestr("module.json", b"{}")
            archive.writestr("libs/arm64-v8a/libapp.so", b"binary")
    return path


def _request(path: Path, **updates: object) -> PublishRequest:
    values: dict[str, object] = {
        "store": StoreName.HARMONYOS,
        "app_alias": "wallet",
        "file": path,
        "release_notes": "修复已知问题",
        "submit": True,
        "confirmed": True,
        "poll_interval_seconds": 5,
        "wait_timeout_seconds": 30,
    }
    values.update(updates)
    return PublishRequest.model_validate(values)


def _publisher(
    *,
    tmp_path: Path,
    rsa_private_key: str,
    backend: HarmonyBackend,
) -> tuple[Publisher, RunRepository, httpx.AsyncClient]:
    account = HuaweiServiceAccount(
        key_id="key-1",
        sub_account="sub-1",
        private_key=rsa_private_key,
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    registration = get_registration(StoreName.HARMONYOS)
    repository = RunRepository(tmp_path / "runs")
    publisher = Publisher(
        adapter=registration.factory(account, http),
        repository=repository,
        target=StoreTarget(
            store=StoreName.HARMONYOS,
            label=registration.label,
            app_id="100000002",
            package_name="com.example.wallet.harmony",
            credential_profile="default",
            language="zh-CN",
        ),
        validator=validate_harmonyos_artifact,
        capabilities=registration.capabilities,
        clock=TickClock(),
        sleeper=_no_sleep,
    )
    return publisher, repository, http


async def _no_sleep(seconds: float) -> None:
    return None


def _assert_no_temporary_secrets(
    result: OperationResult, receipt_text: str, backend: HarmonyBackend
) -> None:
    serialized = result.model_dump_json() + receipt_text
    assert backend.object_id not in serialized
    assert backend.obs_url not in serialized
    assert backend.obs_authorization not in serialized
    assert "temporary-upload-secret" not in serialized


@pytest.mark.asyncio
async def test_app_full_submit_is_end_to_end_and_persists_only_package_id(
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    backend = HarmonyBackend()
    backend.processing_states = ["processing", "ready"]
    publisher, repository, http = _publisher(
        tmp_path=tmp_path,
        rsa_private_key=rsa_private_key,
        backend=backend,
    )

    async with http:
        result = await publisher.publish(_request(_artifact(tmp_path)))

    assert result.ok is True
    assert result.store is StoreName.HARMONYOS
    assert result.stage is PublishStage.SUBMITTED
    receipt = repository.get(result.run_id or "")
    assert receipt.state is RunState.COMPLETED
    assert receipt.artifact_id == "package-42"
    assert backend.upload_calls == 1
    assert backend.processing_calls == 2
    assert backend.submit_calls == 1
    receipt_text = (repository.root / f"{receipt.run_id}.json").read_text(encoding="utf-8")
    _assert_no_temporary_secrets(result, receipt_text, backend)


@pytest.mark.asyncio
async def test_hap_no_submit_stops_when_package_is_ready(
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    backend = HarmonyBackend()
    publisher, repository, http = _publisher(
        tmp_path=tmp_path,
        rsa_private_key=rsa_private_key,
        backend=backend,
    )

    async with http:
        result = await publisher.publish(
            _request(
                _artifact(tmp_path, ".hap"),
                submit=False,
                confirmed=False,
                release_notes=None,
            )
        )

    assert result.ok is True
    assert result.stage is PublishStage.PACKAGE_READY
    assert repository.get(result.run_id or "").state is RunState.COMPLETED
    assert backend.submit_calls == 0
    assert not any(path.endswith("/app-language-info") for _, path in backend.requests)


@pytest.mark.asyncio
async def test_timeout_resume_and_interruption_never_upload_twice(
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    backend = HarmonyBackend()
    backend.processing_states = ["processing", "ready"]
    publisher, repository, http = _publisher(
        tmp_path=tmp_path,
        rsa_private_key=rsa_private_key,
        backend=backend,
    )

    async with http:
        timed_out = await publisher.publish(_request(_artifact(tmp_path), wait_timeout_seconds=5))
        resumed = await publisher.resume(timed_out.run_id or "", poll_interval=5)

    assert timed_out.stage is PublishStage.TIMED_OUT
    assert timed_out.resumable is True
    assert resumed.stage is PublishStage.SUBMITTED
    assert backend.upload_calls == 1
    assert repository.get(resumed.run_id or "").state is RunState.COMPLETED

    second_backend = HarmonyBackend()
    second_backend.cancel_processing_once = True
    second_publisher, second_repository, second_http = _publisher(
        tmp_path=tmp_path / "interrupted",
        rsa_private_key=rsa_private_key,
        backend=second_backend,
    )
    async with second_http:
        interrupted = await second_publisher.publish(_request(_artifact(tmp_path / "interrupted")))
        resumed_after_interrupt = await second_publisher.resume(
            interrupted.run_id or "",
            poll_interval=5,
        )

    assert interrupted.stage is PublishStage.INTERRUPTED
    assert interrupted.resumable is True
    assert resumed_after_interrupt.stage is PublishStage.SUBMITTED
    assert second_backend.upload_calls == 1
    assert second_repository.get(resumed_after_interrupt.run_id or "").state is RunState.COMPLETED


@pytest.mark.asyncio
async def test_submit_compiling_repolls_and_retries_without_upload(
    tmp_path: Path,
    rsa_private_key: str,
) -> None:
    backend = HarmonyBackend()
    backend.submit_compiling_once = True
    publisher, _, http = _publisher(
        tmp_path=tmp_path,
        rsa_private_key=rsa_private_key,
        backend=backend,
    )

    async with http:
        result = await publisher.publish(_request(_artifact(tmp_path)))

    assert result.stage is PublishStage.SUBMITTED
    assert backend.upload_calls == 1
    assert backend.processing_calls == 2
    assert backend.submit_calls == 2
