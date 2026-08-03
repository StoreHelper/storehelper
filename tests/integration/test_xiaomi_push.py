from __future__ import annotations

import asyncio
import json
from email import policy
from email.parser import BytesParser
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from storehelper.credentials.models import XiaomiApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.models import StoreName, StoreTarget
from storehelper.stores.xiaomi.adapter import XiaomiAdapter
from storehelper.stores.xiaomi.auth import XiaomiAuth
from storehelper.stores.xiaomi.client import XiaomiClient
from storehelper.stores.xiaomi.errors import XiaomiVendorError
from storehelper.stores.xiaomi.package import validate_xiaomi_artifact, validate_xiaomi_target


def _credential(certificate: str, *, review_accounts: bool = True) -> XiaomiApiCredential:
    value: dict[str, object] = {
        "username": "developer@example.com",
        "api_secret": "api-secret",
        "public_key_certificate": certificate,
    }
    if review_accounts:
        value["test_accounts"] = {
            "zh_CN": {
                "accounts": [
                    {
                        "login_type": 1,
                        "account": "reviewer@example.com",
                        "password": "review-password",
                        "access_code": "invite-code",
                    }
                ],
                "audit_notes": "Open the demo workspace.",
            }
        }
    return XiaomiApiCredential.model_validate(value)


def _target(icon: Path, **updates: object) -> StoreTarget:
    values: dict[str, object] = {
        "store": StoreName.XIAOMI,
        "label": "Xiaomi App Store",
        "app_id": "com.example.wallet",
        "package_name": "com.example.wallet",
        "credential_profile": "xiaomi-release",
        "language": "zh-CN",
        "app_name": "Example Wallet",
        "icon_path": icon,
        "privacy_url": "https://example.com/privacy",
    }
    values.update(updates)
    return StoreTarget.model_validate(values)


def _files(tmp_path: Path) -> tuple[Path, Path]:
    import zipfile

    apk = tmp_path / "wallet release.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("META-INF/CERT.RSA", b"signature")
    icon = tmp_path / "xiaomi-icon.png"
    icon.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01")
    return apk, icon


def _parts(request: httpx.Request) -> dict[str, tuple[str | None, str | None, bytes]]:
    message = BytesParser(policy=policy.default).parsebytes(
        (f"Content-Type: {request.headers['content-type']}\r\nMIME-Version: 1.0\r\n\r\n").encode()
        + request.content
    )
    result: dict[str, tuple[str | None, str | None, bytes]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        assert isinstance(name, str)
        result[name] = (
            part.get_filename(),
            part.get_content_type(),
            part.get_payload(decode=True),
        )
    return result


def _decrypt_signature(value: str, private_key_pem: str) -> dict[str, object]:
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    assert isinstance(key, rsa.RSAPrivateKey)
    ciphertext = bytes.fromhex(value)
    block_size = key.key_size // 8
    plaintext = b"".join(
        key.decrypt(ciphertext[offset : offset + block_size], padding.PKCS1v15())
        for offset in range(0, len(ciphertext), block_size)
    )
    return json.loads(plaintext)


def _adapter(
    credential: XiaomiApiCredential,
    transport: httpx.AsyncBaseTransport,
) -> tuple[XiaomiAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    return (
        XiaomiAdapter(
            XiaomiClient(
                auth=XiaomiAuth(credential),
                credential=credential,
                http=http,
            )
        ),
        http,
    )


@pytest.mark.asyncio
async def test_push_streams_exact_update_request_and_structured_review_accounts(
    tmp_path: Path,
    rsa_private_key: str,
    rsa_public_certificate: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apk, icon = _files(tmp_path)
    artifact = validate_xiaomi_artifact(apk)
    target = _target(icon)
    validate_xiaomi_target(target)
    expected_apk = apk.read_bytes()
    expected_icon = icon.read_bytes()
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: (_ for _ in ()).throw(AssertionError("upload buffered a file")),
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        requests.append(request)
        return httpx.Response(200, json={"result": 0, "message": "提交成功"})

    adapter, http = _adapter(
        _credential(rsa_public_certificate),
        httpx.MockTransport(handler),
    )
    async with http:
        submission_id = await adapter.publish_atomic(
            target=target,
            artifact=artifact,
            release_notes="修复登录问题",
        )

    assert submission_id == "com.example.wallet"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url == "https://api.developer.xiaomi.com/devupload/dev/push"
    parts = _parts(request)
    assert set(parts) == {"RequestData", "SIG", "apk", "icon"}
    request_data = parts["RequestData"][2].decode()
    outer = json.loads(request_data)
    assert outer["userName"] == "developer@example.com"
    assert outer["synchroType"] == 1
    assert isinstance(outer["appInfo"], str)
    app_info = json.loads(outer["appInfo"])
    assert app_info == {
        "appName": "Example Wallet",
        "packageName": "com.example.wallet",
        "updateDesc": "修复登录问题",
        "privacyUrl": "https://example.com/privacy",
        "suitableType": 0,
        "testAccount": json.dumps(
            {
                "zh_CN": {
                    "accounts": [
                        {
                            "t": 1,
                            "a": "reviewer@example.com",
                            "p": "review-password",
                            "c": "invite-code",
                        }
                    ],
                    "auditNotes": "Open the demo workspace.",
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    assert parts["apk"] == (
        "wallet-release.apk",
        "application/vnd.android.package-archive",
        expected_apk,
    )
    assert parts["icon"] == ("xiaomi-icon.png", "image/png", expected_icon)
    signature = _decrypt_signature(parts["SIG"][2].decode(), rsa_private_key)
    assert [item["name"] for item in signature["sig"]] == ["RequestData", "apk", "icon"]
    assert signature["password"] == "api-secret"


@pytest.mark.asyncio
async def test_push_omits_test_account_when_not_configured(
    tmp_path: Path,
    rsa_public_certificate: str,
) -> None:
    apk, icon = _files(tmp_path)
    request_data: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        request_data.append(_parts(request)["RequestData"][2].decode())
        return httpx.Response(200, json={"result": 0})

    adapter, http = _adapter(
        _credential(rsa_public_certificate, review_accounts=False),
        httpx.MockTransport(handler),
    )
    async with http:
        await adapter.publish_atomic(
            target=_target(icon),
            artifact=validate_xiaomi_artifact(apk),
            release_notes="Fixes",
        )

    app_info = json.loads(json.loads(request_data[0])["appInfo"])
    assert "testAccount" not in app_info


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "code", "exit_code"),
    [
        ("vendor", "XIAOMI_APK_REJECTED", ExitCode.VENDOR_REJECTION),
        ("redirect", "XIAOMI_REDIRECT", ExitCode.NETWORK),
        ("non-json", "XIAOMI_RESPONSE_INVALID", ExitCode.VENDOR_REJECTION),
        ("http", "XIAOMI_AUTHENTICATION_FAILED", ExitCode.AUTHENTICATION),
        ("response-loss", "XIAOMI_NETWORK_ERROR", ExitCode.NETWORK),
    ],
)
async def test_push_failures_are_safe_and_never_retried(
    tmp_path: Path,
    rsa_public_certificate: str,
    mode: str,
    code: str,
    exit_code: ExitCode,
) -> None:
    apk, icon = _files(tmp_path)
    attempts = 0
    leaked = "api_secret=must-never-print"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        await request.aread()
        if mode == "vendor":
            return httpx.Response(200, json={"result": -92, "message": leaked})
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "https://attacker.example"})
        if mode == "non-json":
            return httpx.Response(200, text=leaked)
        if mode == "http":
            return httpx.Response(403, json={"message": leaked})
        raise httpx.ReadError(leaked, request=request)

    adapter, http = _adapter(
        _credential(rsa_public_certificate),
        httpx.MockTransport(handler),
    )
    async with http:
        with pytest.raises(XiaomiVendorError) as raised:
            await adapter.publish_atomic(
                target=_target(icon),
                artifact=validate_xiaomi_artifact(apk),
                release_notes="Fixes",
            )

    assert attempts == 1
    assert raised.value.code == code
    assert raised.value.exit_code is exit_code
    assert leaked not in str(raised.value)


@pytest.mark.asyncio
async def test_push_cancellation_propagates_without_a_second_request(
    tmp_path: Path,
    rsa_public_certificate: str,
) -> None:
    apk, icon = _files(tmp_path)
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise asyncio.CancelledError

    adapter, http = _adapter(
        _credential(rsa_public_certificate),
        httpx.MockTransport(handler),
    )
    async with http:
        with pytest.raises(asyncio.CancelledError):
            await adapter.publish_atomic(
                target=_target(icon),
                artifact=validate_xiaomi_artifact(apk),
                release_notes="Fixes",
            )

    assert attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_updates", "notes", "code"),
    [
        ({"app_name": None}, "Fixes", "XIAOMI_TARGET_INVALID"),
        ({"privacy_url": None}, "Fixes", "XIAOMI_TARGET_INVALID"),
        ({"privacy_url": "http://example.com/privacy"}, "Fixes", "XIAOMI_TARGET_INVALID"),
        ({"icon_path": None}, "Fixes", "XIAOMI_TARGET_INVALID"),
        ({}, None, "XIAOMI_RELEASE_NOTES_INVALID"),
        ({}, "x" * 501, "XIAOMI_RELEASE_NOTES_INVALID"),
    ],
)
async def test_push_rejects_invalid_target_and_notes_before_network(
    tmp_path: Path,
    rsa_public_certificate: str,
    target_updates: dict[str, object],
    notes: str | None,
    code: str,
) -> None:
    apk, icon = _files(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid push attempted network access")

    adapter, http = _adapter(
        _credential(rsa_public_certificate),
        httpx.MockTransport(handler),
    )
    async with http:
        with pytest.raises(XiaomiVendorError) as raised:
            await adapter.publish_atomic(
                target=_target(icon, **target_updates),
                artifact=validate_xiaomi_artifact(apk),
                release_notes=notes,
            )

    assert raised.value.code == code
