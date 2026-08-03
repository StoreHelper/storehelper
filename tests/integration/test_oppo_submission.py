from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from storehelper.credentials.models import OppoApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.models import StoreName, StoreTarget
from storehelper.stores.oppo.adapter import OppoAdapter
from storehelper.stores.oppo.auth import OppoAuth
from storehelper.stores.oppo.client import OppoClient
from storehelper.stores.oppo.errors import OppoVendorError
from storehelper.stores.oppo.package import OppoArtifactInfo, validate_oppo_artifact


def _application(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "pkg_name": "com.example.wallet",
        "version_code": "42",
        "audit_status": 111,
        "app_name": "Example Wallet",
        "second_category_id": "463",
        "third_category_id": "6648",
        "summary": "Safe payments",
        "detail_desc": "A complete existing application description.",
        "privacy_source_url": "https://example.com/privacy",
        "icon_url": "https://cdn.example.com/icon.png",
        "pic_url": "https://cdn.example.com/one.png,https://cdn.example.com/two.png",
        "age_level": "18",
        "adaptive_equipment": "4",
        "copyright_url": "https://cdn.example.com/copyright.pdf",
        "business_username": "Release Owner",
        "business_email": "release@example.com",
        "business_mobile": "13800138000",
    }
    value.update(updates)
    return value


def _target(**updates: object) -> StoreTarget:
    value: dict[str, object] = {
        "store": StoreName.OPPO,
        "label": "OPPO Software Store",
        "app_id": "com.example.wallet",
        "package_name": "com.example.wallet",
        "credential_profile": "oppo-wallet",
        "language": "zh-CN",
        "version_code": 43,
    }
    value.update(updates)
    return StoreTarget.model_validate(value)


def _artifact(tmp_path: Path) -> OppoArtifactInfo:
    apk = tmp_path / "wallet release.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
    return validate_oppo_artifact(apk)


def _adapter(
    transport: httpx.AsyncBaseTransport,
) -> tuple[OppoAdapter, httpx.AsyncClient]:
    credential = OppoApiCredential(
        client_id=SecretStr("oppo-client-sensitive"),
        client_secret=SecretStr("oppo-secret-sensitive"),
    )
    auth = OppoAuth(credential, clock=lambda: 1_700_000_000.0)
    auth.cache_token("oppo-access-sensitive", expires_in=7200)
    http = httpx.AsyncClient(transport=transport)
    return OppoAdapter(OppoClient(credential=credential, auth=auth, http=http)), http


@pytest.mark.asyncio
async def test_stages_fresh_listing_and_upload_then_submits_exact_signed_form(
    tmp_path: Path,
) -> None:
    final_forms: list[dict[str, list[str]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/resource/v1/app/info":
            return httpx.Response(200, json={"errno": 0, "data": _application()})
        if request.url.path == "/resource/v1/upload/get-upload-url":
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {
                        "upload_url": "https://upload.oppomobile.com/file",
                        "sign": "upload-sign-sensitive",
                    },
                },
            )
        if request.url.host == "upload.oppomobile.com":
            await request.aread()
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {"url": "https://cdn.oppomobile.com/private/wallet.apk"},
                },
            )
        body = (await request.aread()).decode()
        final_forms.append(parse_qs(body, keep_blank_values=True))
        return httpx.Response(200, json={"errno": 0, "data": {"task_id": "task-1"}})

    adapter, http = _adapter(httpx.MockTransport(handler))
    notes = "n" * 450
    async with http:
        verified = await adapter.verify(target=_target())
        await adapter.stage_submission(
            target=_target(),
            artifact=_artifact(tmp_path),
            release_notes=notes,
        )
        submission_id = await adapter.commit_staged_submission(target=_target())

    assert verified.app_id == "com.example.wallet"
    assert submission_id == "com.example.wallet"
    assert len(final_forms) == 1
    form = {key: values[0] for key, values in final_forms[0].items()}
    assert form["pkg_name"] == "com.example.wallet"
    assert form["version_code"] == "43"
    assert json.loads(form["apk_url"]) == [
        {
            "url": "https://cdn.oppomobile.com/private/wallet.apk",
            "md5": _artifact(tmp_path).md5,
        }
    ]
    assert form["app_name"] == "Example Wallet"
    assert form["second_category_id"] == "463"
    assert form["third_category_id"] == "6648"
    assert form["summary"] == "Safe payments"
    assert form["detail_desc"] == "A complete existing application description."
    assert form["privacy_source_url"] == "https://example.com/privacy"
    assert form["icon_url"] == "https://cdn.example.com/icon.png"
    assert form["pic_url"].startswith("https://cdn.example.com/one.png")
    assert form["age_level"] == "18"
    assert form["adaptive_equipment"] == "4"
    assert form["copyright_url"].endswith("copyright.pdf")
    assert form["business_username"] == "Release Owner"
    assert form["business_email"] == "release@example.com"
    assert form["business_mobile"] == "13800138000"
    assert form["update_desc"] == notes
    assert form["test_desc"] == notes[:400]
    assert form["online_type"] == "1"
    assert form["access_token"] == "oppo-access-sensitive"
    assert form["timestamp"] == "1700000000"
    assert len(form["api_sign"]) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_updates", "application_updates", "notes", "code"),
    [
        ({"store": StoreName.XIAOMI}, {}, "Fixes", "OPPO_TARGET_INVALID"),
        ({"app_id": "com.example.other"}, {}, "Fixes", "OPPO_TARGET_INVALID"),
        ({"version_code": 42}, {}, "Fixes", "OPPO_VERSION_CONFLICT"),
        ({"version_code": 41}, {}, "Fixes", "OPPO_VERSION_CONFLICT"),
        ({}, {"audit_status": 1}, "Fixes", "OPPO_REVIEW_CONFLICT"),
        ({}, {}, "", "OPPO_RELEASE_NOTES_INVALID"),
        ({}, {}, "n" * 501, "OPPO_RELEASE_NOTES_INVALID"),
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
        if request.url.path == "/resource/v1/app/info":
            return httpx.Response(
                200,
                json={"errno": 0, "data": _application(**application_updates)},
            )
        raise AssertionError("invalid OPPO submission progressed beyond verification")

    adapter, http = _adapter(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(OppoVendorError) as raised:
            if code == "OPPO_RELEASE_NOTES_INVALID":
                await adapter.stage_submission(
                    target=_target(**target_updates),
                    artifact=_artifact(tmp_path),
                    release_notes=notes,
                )
            else:
                await adapter.verify(target=_target(**target_updates))

    assert raised.value.code == code


@pytest.mark.asyncio
async def test_commit_refuses_missing_or_mismatched_staged_context(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/resource/v1/app/info":
            return httpx.Response(200, json={"errno": 0, "data": _application()})
        if request.url.path == "/resource/v1/upload/get-upload-url":
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {
                        "upload_url": "https://upload.oppomobile.com/file",
                        "sign": "upload-sign",
                    },
                },
            )
        if request.url.host == "upload.oppomobile.com":
            return httpx.Response(
                200,
                json={"errno": 0, "data": {"url": "https://cdn.oppomobile.com/file.apk"}},
            )
        raise AssertionError("invalid staged context attempted final submission")

    adapter, http = _adapter(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(OppoVendorError) as missing:
            await adapter.commit_staged_submission(target=_target())
        await adapter.stage_submission(
            target=_target(),
            artifact=_artifact(tmp_path),
            release_notes="Fixes",
        )
        with pytest.raises(OppoVendorError) as mismatch:
            await adapter.commit_staged_submission(target=_target(version_code=44))

    assert missing.value.code == "OPPO_STAGED_CONTEXT_MISSING"
    assert mismatch.value.code == "OPPO_STAGED_CONTEXT_MISMATCH"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["vendor", "redirect", "response-loss", "server-error"])
async def test_final_submission_is_single_shot_and_safe_on_every_failure(
    tmp_path: Path,
    mode: str,
) -> None:
    final_calls = 0
    leaked = "access_token=must-never-print upload-sign=must-never-print"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal final_calls
        if request.url.path == "/resource/v1/app/info":
            return httpx.Response(200, json={"errno": 0, "data": _application()})
        if request.url.path == "/resource/v1/upload/get-upload-url":
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {
                        "upload_url": "https://upload.oppomobile.com/file",
                        "sign": "upload-sign",
                    },
                },
            )
        if request.url.host == "upload.oppomobile.com":
            return httpx.Response(
                200,
                json={"errno": 0, "data": {"url": "https://cdn.oppomobile.com/file.apk"}},
            )
        final_calls += 1
        if mode == "vendor":
            return httpx.Response(200, json={"errno": 910006, "data": {"message": leaked}})
        if mode == "redirect":
            return httpx.Response(302, headers={"location": f"https://attacker.example/{leaked}"})
        if mode == "server-error":
            return httpx.Response(503, json={"message": leaked})
        raise httpx.ReadError(leaked, request=request)

    adapter, http = _adapter(httpx.MockTransport(handler))
    async with http:
        await adapter.stage_submission(
            target=_target(),
            artifact=_artifact(tmp_path),
            release_notes="Fixes",
        )
        with pytest.raises(OppoVendorError) as raised:
            await adapter.commit_staged_submission(target=_target())
        with pytest.raises(OppoVendorError) as consumed:
            await adapter.commit_staged_submission(target=_target())

    assert final_calls == 1
    assert leaked not in str(raised.value)
    assert consumed.value.code == "OPPO_STAGED_CONTEXT_MISSING"
    if mode == "response-loss":
        assert raised.value.code == "OPPO_SUBMISSION_UNCERTAIN"
        assert raised.value.exit_code is ExitCode.NETWORK


@pytest.mark.asyncio
async def test_final_submission_cancellation_consumes_staged_context(tmp_path: Path) -> None:
    final_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal final_calls
        if request.url.path == "/resource/v1/app/info":
            return httpx.Response(200, json={"errno": 0, "data": _application()})
        if request.url.path == "/resource/v1/upload/get-upload-url":
            return httpx.Response(
                200,
                json={
                    "errno": 0,
                    "data": {
                        "upload_url": "https://upload.oppomobile.com/file",
                        "sign": "upload-sign",
                    },
                },
            )
        if request.url.host == "upload.oppomobile.com":
            return httpx.Response(
                200,
                json={"errno": 0, "data": {"url": "https://cdn.oppomobile.com/file.apk"}},
            )
        final_calls += 1
        raise asyncio.CancelledError

    adapter, http = _adapter(httpx.MockTransport(handler))
    async with http:
        await adapter.stage_submission(
            target=_target(),
            artifact=_artifact(tmp_path),
            release_notes="Fixes",
        )
        with pytest.raises(asyncio.CancelledError):
            await adapter.commit_staged_submission(target=_target())
        with pytest.raises(OppoVendorError) as consumed:
            await adapter.commit_staged_submission(target=_target())

    assert final_calls == 1
    assert consumed.value.code == "OPPO_STAGED_CONTEXT_MISSING"
