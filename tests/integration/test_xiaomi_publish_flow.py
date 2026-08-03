from __future__ import annotations

import asyncio
import io
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

import httpx
import pytest

from storehelper.credentials.models import XiaomiApiCredential
from storehelper.domain.models import PublishRequest, PublishStage
from storehelper.output.renderers import render_error, render_result
from storehelper.publishing.service import Publisher, PublishingError
from storehelper.runs.models import RunState
from storehelper.runs.repository import RunRepository
from storehelper.stores.models import StoreName, StoreTarget
from storehelper.stores.registry import get_registration
from storehelper.stores.xiaomi.adapter import XiaomiAdapter
from storehelper.stores.xiaomi.auth import XiaomiAuth
from storehelper.stores.xiaomi.client import XiaomiClient
from storehelper.stores.xiaomi.errors import XiaomiVendorError


class HardCrash(BaseException):
    pass


def _project(tmp_path: Path) -> tuple[Path, Path]:
    apk = tmp_path / "wallet.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01")
    return apk, icon


def _credential(certificate: str) -> XiaomiApiCredential:
    return XiaomiApiCredential.model_validate(
        {
            "username": "developer@example.com",
            "api_secret": "known-xiaomi-api-secret",
            "public_key_certificate": certificate,
            "test_accounts": {
                "zh_CN": {
                    "accounts": [
                        {
                            "login_type": 1,
                            "account": "known-review-account",
                            "password": "known-review-password",
                            "access_code": "known-access-code",
                        }
                    ],
                    "audit_notes": "known-audit-note",
                }
            },
        }
    )


def _target(icon: Path) -> StoreTarget:
    return StoreTarget(
        store=StoreName.XIAOMI,
        label="Xiaomi App Store",
        app_id="com.example.wallet",
        package_name="com.example.wallet",
        credential_profile="release",
        language="zh-CN",
        app_name="Example Wallet",
        icon_path=icon,
        privacy_url="https://example.com/privacy",
    )


def _request(apk: Path, *, dry_run: bool = False) -> PublishRequest:
    return PublishRequest(
        app_alias="wallet",
        store=StoreName.XIAOMI,
        file=apk,
        release_notes="Security fixes",
        submit=not dry_run,
        dry_run=dry_run,
        confirmed=not dry_run,
        poll_interval_seconds=5,
        wait_timeout_seconds=5,
    )


def _multipart_value(request: httpx.Request, name: str) -> str:
    message = BytesParser(policy=policy.default).parsebytes(
        (f"Content-Type: {request.headers['content-type']}\r\nMIME-Version: 1.0\r\n\r\n").encode()
        + request.content
    )
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") == name:
            return part.get_payload(decode=True).decode()
    raise AssertionError(f"multipart field not found: {name}")


class XiaomiBackend:
    def __init__(self) -> None:
        self.mode = "success"
        self.query_calls = 0
        self.push_calls = 0
        self.request_data: str | None = None
        self.signature: str | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/dev/query"):
            self.query_calls += 1
            return httpx.Response(
                200,
                json={
                    "result": 0,
                    "updateVersion": True,
                    "packageInfo": {
                        "appName": "Example Wallet",
                        "packageName": "com.example.wallet",
                    },
                },
            )
        assert request.url.path.endswith("/dev/push")
        self.push_calls += 1
        await request.aread()
        self.request_data = _multipart_value(request, "RequestData")
        self.signature = _multipart_value(request, "SIG")
        if self.mode == "vendor":
            return httpx.Response(
                200,
                json={"result": -92, "message": "api_secret=known-raw-vendor-marker"},
            )
        if self.mode == "loss":
            raise httpx.ReadError(
                "api_secret=known-raw-vendor-marker",
                request=request,
            )
        if self.mode == "cancel":
            raise asyncio.CancelledError
        if self.mode == "crash":
            raise HardCrash
        return httpx.Response(200, json={"result": 0})


def _publisher(
    tmp_path: Path,
    certificate: str,
    backend: XiaomiBackend,
) -> tuple[Publisher, httpx.AsyncClient]:
    credential = _credential(certificate)
    http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    adapter = XiaomiAdapter(
        XiaomiClient(
            auth=XiaomiAuth(credential),
            credential=credential,
            http=http,
        )
    )
    registration = get_registration(StoreName.XIAOMI)
    _, icon = _project(tmp_path)
    return (
        Publisher(
            adapter=adapter,
            repository=RunRepository(tmp_path / "runs"),
            target=_target(icon),
            validator=registration.validator,
            capabilities=registration.capabilities,
            target_validator=registration.target_validator,
        ),
        http,
    )


@pytest.mark.asyncio
async def test_success_and_dry_run_keep_xiaomi_secrets_out_of_receipts_and_output(
    tmp_path: Path,
    rsa_public_certificate: str,
) -> None:
    backend = XiaomiBackend()
    publisher, http = _publisher(tmp_path, rsa_public_certificate, backend)
    apk = tmp_path / "wallet.apk"

    async with http:
        dry = await publisher.publish(_request(apk, dry_run=True))
        submitted = await publisher.publish(_request(apk))

    assert dry.stage is PublishStage.COMPLETED
    assert submitted.stage is PublishStage.SUBMITTED
    assert backend.query_calls == backend.push_calls == 1
    output = io.StringIO()
    render_result(submitted, output="json", stdout=output)
    receipt = (publisher.repository.root / f"{submitted.run_id}.json").read_text(encoding="utf-8")
    forbidden = (
        "known-xiaomi-api-secret",
        rsa_public_certificate,
        "known-review-account",
        "known-review-password",
        "known-access-code",
        "known-audit-note",
        backend.request_data or "missing-request-data",
        backend.signature or "missing-signature",
    )
    for secret in forbidden:
        assert secret not in output.getvalue()
        assert secret not in receipt


@pytest.mark.asyncio
async def test_response_loss_becomes_uncertain_blocks_duplicate_then_allows_deliberate_retry(
    tmp_path: Path,
    rsa_public_certificate: str,
) -> None:
    backend = XiaomiBackend()
    backend.mode = "loss"
    publisher, http = _publisher(tmp_path, rsa_public_certificate, backend)
    apk = tmp_path / "wallet.apk"

    async with http:
        with pytest.raises(XiaomiVendorError) as raised:
            await publisher.publish(_request(apk))
        receipt = publisher.repository.list()[0]
        assert receipt.state is RunState.SUBMISSION_UNCERTAIN
        assert receipt.resumable is False
        output = io.StringIO()
        render_error(raised.value, output="json", stdout=output)
        assert "known-raw-vendor-marker" not in output.getvalue()
        calls = (backend.query_calls, backend.push_calls)
        blocked = await publisher.publish(_request(apk))
        assert blocked.stage is PublishStage.INTERRUPTED
        assert blocked.resumable is False
        assert (backend.query_calls, backend.push_calls) == calls
        with pytest.raises(PublishingError) as resume_error:
            await publisher.resume(receipt.run_id)
        assert resume_error.value.code == "ATOMIC_RUN_NOT_RESUMABLE"

        assert publisher.repository.delete(receipt.run_id) is True
        backend.mode = "success"
        retried = await publisher.publish(_request(apk))

    assert retried.stage is PublishStage.SUBMITTED
    assert backend.push_calls == 2


@pytest.mark.asyncio
async def test_cancellation_is_uncertain_and_non_resumable(
    tmp_path: Path,
    rsa_public_certificate: str,
) -> None:
    backend = XiaomiBackend()
    backend.mode = "cancel"
    publisher, http = _publisher(tmp_path, rsa_public_certificate, backend)

    async with http:
        result = await publisher.publish(_request(tmp_path / "wallet.apk"))

    assert result.stage is PublishStage.INTERRUPTED
    assert result.resumable is False
    assert publisher.repository.get(result.run_id or "").state is RunState.SUBMISSION_UNCERTAIN
    assert backend.push_calls == 1


@pytest.mark.asyncio
async def test_hard_crash_leaves_started_receipt_that_blocks_same_artifact(
    tmp_path: Path,
    rsa_public_certificate: str,
) -> None:
    backend = XiaomiBackend()
    backend.mode = "crash"
    publisher, http = _publisher(tmp_path, rsa_public_certificate, backend)
    apk = tmp_path / "wallet.apk"

    async with http:
        with pytest.raises(HardCrash):
            await publisher.publish(_request(apk))
        receipt = publisher.repository.list()[0]
        assert receipt.state is RunState.SUBMISSION_STARTED
        calls = (backend.query_calls, backend.push_calls)
        blocked = await publisher.publish(_request(apk))

    assert blocked.stage is PublishStage.INTERRUPTED
    assert blocked.resumable is False
    assert (backend.query_calls, backend.push_calls) == calls


@pytest.mark.asyncio
async def test_deterministic_vendor_rejection_fails_without_uncertain_lock(
    tmp_path: Path,
    rsa_public_certificate: str,
) -> None:
    backend = XiaomiBackend()
    backend.mode = "vendor"
    publisher, http = _publisher(tmp_path, rsa_public_certificate, backend)
    apk = tmp_path / "wallet.apk"

    async with http:
        with pytest.raises(XiaomiVendorError) as raised:
            await publisher.publish(_request(apk))
        assert raised.value.code == "XIAOMI_APK_REJECTED"
        failed = publisher.repository.list()[0]
        assert failed.state is RunState.FAILED
        backend.mode = "success"
        retried = await publisher.publish(_request(apk))

    assert retried.stage is PublishStage.SUBMITTED
    assert backend.push_calls == 2
