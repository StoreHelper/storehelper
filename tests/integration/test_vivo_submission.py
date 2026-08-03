from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from storehelper.credentials.models import VivoApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.models import StoreName, StoreTarget
from storehelper.stores.vivo.adapter import VivoAdapter
from storehelper.stores.vivo.auth import VivoAuth
from storehelper.stores.vivo.client import VivoClient
from storehelper.stores.vivo.errors import VivoVendorError
from storehelper.stores.vivo.package import VivoArtifactInfo, validate_vivo_artifact


def _application(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "packageName": "com.example.wallet",
        "versionCode": "42",
        "status": 5,
    }
    value.update(updates)
    return value


def _target(**updates: object) -> StoreTarget:
    value: dict[str, object] = {
        "store": StoreName.VIVO,
        "label": "vivo App Store",
        "app_id": "com.example.wallet",
        "package_name": "com.example.wallet",
        "credential_profile": "vivo-wallet",
        "language": "zh-CN",
        "version_code": 43,
    }
    value.update(updates)
    return StoreTarget.model_validate(value)


def _artifact(tmp_path: Path) -> VivoArtifactInfo:
    apk = tmp_path / "wallet.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
    return validate_vivo_artifact(apk)


def _adapter(transport: httpx.AsyncBaseTransport) -> tuple[VivoAdapter, httpx.AsyncClient]:
    credential = VivoApiCredential(
        access_key=SecretStr("vivo-access-sensitive"),
        secret_key=SecretStr("vivo-secret-sensitive"),
    )
    http = httpx.AsyncClient(transport=transport)
    client = VivoClient(auth=VivoAuth(credential, clock=lambda: 1_700_000_000.0), http=http)
    return VivoAdapter(client), http


async def _form(request: httpx.Request) -> dict[str, str]:
    body = (await request.aread()).decode()
    return {key: values[0] for key, values in parse_qs(body, keep_blank_values=True).items()}


@pytest.mark.asyncio
async def test_stages_fresh_query_and_upload_then_submits_only_version_fields(
    tmp_path: Path,
) -> None:
    query_calls = 0
    final_forms: list[dict[str, str]] = []
    artifact = _artifact(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal query_calls
        if request.headers["content-type"].startswith("multipart/form-data"):
            await request.aread()
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"serialnumber": "serial-sensitive", "fileMd5": artifact.md5},
                },
            )
        form = await _form(request)
        if form["method"] == "app.query.details":
            query_calls += 1
            return httpx.Response(200, json={"code": 0, "data": _application()})
        final_forms.append(form)
        return httpx.Response(200, json={"code": 0, "data": {}})

    adapter, http = _adapter(httpx.MockTransport(handler))
    notes = "修复若干已知问题并优化体验"
    async with http:
        verified = await adapter.verify(target=_target())
        await adapter.stage_submission(target=_target(), artifact=artifact, release_notes=notes)
        submission_id = await adapter.commit_staged_submission(target=_target())

    assert verified.app_id == "com.example.wallet"
    assert query_calls == 2
    assert submission_id == "com.example.wallet"
    assert len(final_forms) == 1
    form = final_forms[0]
    business_keys = {
        "packageName",
        "versionCode",
        "apk",
        "fileMd5",
        "onlineType",
        "compatibleDevice",
        "updateDesc",
    }
    common_keys = {
        "method",
        "access_key",
        "timestamp",
        "format",
        "v",
        "sign_method",
        "target_app_key",
        "sign",
    }
    assert set(form) == business_keys | common_keys
    assert form["method"] == "app.sync.update.app"
    assert form["packageName"] == "com.example.wallet"
    assert form["versionCode"] == "43"
    assert form["apk"] == "serial-sensitive"
    assert form["fileMd5"] == artifact.md5
    assert form["onlineType"] == "1"
    assert form["compatibleDevice"] == "1"
    assert form["updateDesc"] == notes
    for forbidden in ("appName", "detailDesc", "appClassify", "icon", "screenshot"):
        assert forbidden not in form


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_updates", "application_updates", "notes", "code"),
    [
        ({"store": StoreName.OPPO}, {}, "修复若干问题", "VIVO_TARGET_INVALID"),
        ({"app_id": "com.example.other"}, {}, "修复若干问题", "VIVO_TARGET_INVALID"),
        ({"version_code": 42}, {}, "修复若干问题", "VIVO_VERSION_CONFLICT"),
        ({"version_code": 41}, {}, "修复若干问题", "VIVO_VERSION_CONFLICT"),
        ({}, {"status": 1}, "修复若干问题", "VIVO_REVIEW_CONFLICT"),
        ({}, {"status": 2}, "修复若干问题", "VIVO_REVIEW_CONFLICT"),
        ({}, {"status": 6}, "修复若干问题", "VIVO_REVIEW_CONFLICT"),
        ({}, {}, "短", "VIVO_RELEASE_NOTES_INVALID"),
        ({}, {}, "n" * 201, "VIVO_RELEASE_NOTES_INVALID"),
    ],
)
async def test_verify_and_stage_reject_invalid_target_version_state_and_notes(
    tmp_path: Path,
    target_updates: dict[str, object],
    application_updates: dict[str, object],
    notes: str,
    code: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 0, "data": _application(**application_updates)},
        )

    adapter, http = _adapter(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(VivoVendorError) as raised:
            if code == "VIVO_RELEASE_NOTES_INVALID":
                await adapter.stage_submission(
                    target=_target(**target_updates),
                    artifact=_artifact(tmp_path),
                    release_notes=notes,
                )
            else:
                await adapter.verify(target=_target(**target_updates))

    assert raised.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [3, 4, 5])
async def test_verify_allows_approved_rejected_and_online_existing_states(status: int) -> None:
    adapter, http = _adapter(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"code": 0, "data": _application(status=status)}
            )
        )
    )
    async with http:
        verified = await adapter.verify(target=_target())

    assert verified.package_name == "com.example.wallet"


@pytest.mark.asyncio
async def test_commit_refuses_missing_or_mismatched_staged_context(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["content-type"].startswith("multipart/form-data"):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"serialnumber": "serial", "fileMd5": artifact.md5}},
            )
        return httpx.Response(200, json={"code": 0, "data": _application()})

    adapter, http = _adapter(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(VivoVendorError) as missing:
            await adapter.commit_staged_submission(target=_target())
        await adapter.stage_submission(
            target=_target(), artifact=artifact, release_notes="修复若干问题"
        )
        with pytest.raises(VivoVendorError) as mismatch:
            await adapter.commit_staged_submission(target=_target(version_code=44))

    assert missing.value.code == "VIVO_STAGED_CONTEXT_MISSING"
    assert mismatch.value.code == "VIVO_STAGED_CONTEXT_MISMATCH"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["vendor", "redirect", "response-loss", "server"])
async def test_final_submission_is_single_shot_and_consumes_context(
    tmp_path: Path, mode: str
) -> None:
    artifact = _artifact(tmp_path)
    final_calls = 0
    leaked = "access_key=must-never-print secret_key=must-never-print"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal final_calls
        if request.headers["content-type"].startswith("multipart/form-data"):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"serialnumber": "serial", "fileMd5": artifact.md5}},
            )
        form = await _form(request)
        if form["method"] == "app.query.details":
            return httpx.Response(200, json={"code": 0, "data": _application()})
        final_calls += 1
        if mode == "vendor":
            return httpx.Response(200, json={"code": 9, "subCode": "B0302", "msg": leaked})
        if mode == "redirect":
            return httpx.Response(302, headers={"location": f"https://attacker.example/{leaked}"})
        if mode == "server":
            return httpx.Response(503, json={"msg": leaked})
        raise httpx.ReadError(leaked, request=request)

    adapter, http = _adapter(httpx.MockTransport(handler))
    async with http:
        await adapter.stage_submission(
            target=_target(), artifact=artifact, release_notes="修复若干问题"
        )
        with pytest.raises(VivoVendorError) as raised:
            await adapter.commit_staged_submission(target=_target())
        with pytest.raises(VivoVendorError) as consumed:
            await adapter.commit_staged_submission(target=_target())

    assert final_calls == 1
    assert leaked not in str(raised.value)
    assert consumed.value.code == "VIVO_STAGED_CONTEXT_MISSING"
    if mode == "response-loss":
        assert raised.value.code == "VIVO_SUBMISSION_UNCERTAIN"
        assert raised.value.exit_code is ExitCode.NETWORK


@pytest.mark.asyncio
async def test_final_submission_cancellation_consumes_staged_context(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    final_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal final_calls
        if request.headers["content-type"].startswith("multipart/form-data"):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"serialnumber": "serial", "fileMd5": artifact.md5}},
            )
        form = await _form(request)
        if form["method"] == "app.query.details":
            return httpx.Response(200, json={"code": 0, "data": _application()})
        final_calls += 1
        raise asyncio.CancelledError

    adapter, http = _adapter(httpx.MockTransport(handler))
    async with http:
        await adapter.stage_submission(
            target=_target(), artifact=artifact, release_notes="修复若干问题"
        )
        with pytest.raises(asyncio.CancelledError):
            await adapter.commit_staged_submission(target=_target())
        with pytest.raises(VivoVendorError) as consumed:
            await adapter.commit_staged_submission(target=_target())

    assert final_calls == 1
    assert consumed.value.code == "VIVO_STAGED_CONTEXT_MISSING"
