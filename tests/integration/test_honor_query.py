from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest

from storehelper.credentials.models import HonorApiCredential
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.honor.adapter import HonorAdapter
from storehelper.stores.honor.auth import HonorAuth
from storehelper.stores.honor.client import HonorClient
from storehelper.stores.honor.errors import HonorVendorError
from storehelper.stores.models import ReviewStatus, StoreName, StoreTarget

Sleeper = Callable[[float], Awaitable[None]]


def _credential() -> HonorApiCredential:
    return HonorApiCredential(client_id="known-honor-client", client_secret="known-honor-secret")


def _target(*, version_code: int = 43, language: str = "zh-CN") -> StoreTarget:
    return StoreTarget(
        store=StoreName.HONOR,
        label="HONOR App Market",
        app_id="com.example.wallet",
        package_name="com.example.wallet",
        credential_profile="release",
        language=language,
        version_code=version_code,
    )


def _detail(
    *, version_code: int = 42, package_name: str = "com.example.wallet"
) -> dict[str, object]:
    return {
        "basicInfo": {
            "appId": 123456,
            "packageName": package_name,
            "secretKey": "must-never-leak",
        },
        "languageInfo": [
            {
                "languageId": "zh-CN",
                "appName": "示例钱包",
                "intro": "已有应用介绍",
                "briefIntro": "已有简介",
                "newFeature": "旧版本说明",
            }
        ],
        "publishInfo": {"releaseType": 1},
        "fileInfo": [
            {
                "fileName": "old.apk",
                "fileType": 100,
                "fileUrl": "https://example.invalid/old.apk",
                "fileSha256": "a" * 64,
            }
        ],
        "releaseInfo": {"versionName": "1.0.0", "versionCode": version_code},
    }


def _release(*, audit_result: int = 1, version_code: int | None = 42) -> dict[str, object]:
    return {
        "appId": 123456,
        "releaseId": "release-42",
        "versionName": "1.0.0",
        "versionCode": version_code,
        "auditResult": audit_result,
        "auditMessage": "must-not-be-copied",
        "auditAttachment": ["https://example.invalid/private-review.png"],
    }


class ReadBackend:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.detail = _detail()
        self.release = _release()
        self.app_ids: list[dict[str, object]] = [
            {"appId": 123456, "packageName": "com.example.wallet"}
        ]

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "iam.developer.honor.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "known-honor-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        assert request.headers["authorization"] == "Bearer known-honor-token"
        if request.url.path.endswith("/get-app-id"):
            assert dict(request.url.params) == {"pkgName": "com.example.wallet"}
            return httpx.Response(200, json={"code": 0, "msg": "ok", "data": self.app_ids})
        if request.url.path.endswith("/get-app-detail"):
            assert dict(request.url.params) == {"appId": "123456"}
            return httpx.Response(200, json={"code": 0, "msg": "ok", "data": self.detail})
        if request.url.path.endswith("/get-app-current-release"):
            assert dict(request.url.params) == {"appId": "123456"}
            return httpx.Response(200, json={"code": 0, "msg": "ok", "data": self.release})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


def _adapter(
    backend: ReadBackend | httpx.MockTransport,
    *,
    sleeper: Sleeper | None = None,
) -> tuple[HonorAdapter, httpx.AsyncClient]:
    transport = (
        backend if isinstance(backend, httpx.MockTransport) else httpx.MockTransport(backend)
    )
    http = httpx.AsyncClient(transport=transport)
    kwargs = {} if sleeper is None else {"sleeper": sleeper}
    client = HonorClient(auth=HonorAuth(_credential(), http), http=http, **kwargs)
    return HonorAdapter(client), http


@pytest.mark.asyncio
async def test_honor_verify_uses_exact_fixed_reads_and_ignores_sensitive_snapshot_fields() -> None:
    backend = ReadBackend()
    adapter, http = _adapter(backend)
    try:
        verified = await adapter.verify(target=_target())
        status = await adapter.review_status(target=_target())
    finally:
        await http.aclose()

    assert verified.app_id == "123456"
    assert verified.package_name == "com.example.wallet"
    assert status is ReviewStatus.APPROVED
    paths = [request.url.path.rsplit("/", 1)[-1] for request in backend.requests]
    assert paths == [
        "token",
        "get-app-id",
        "get-app-detail",
        "get-app-current-release",
        "get-app-id",
        "get-app-current-release",
    ]
    assert "must-never-leak" not in repr(adapter)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configure, expected_code",
    [
        (lambda backend: setattr(backend, "app_ids", []), "HONOR_APP_NOT_FOUND"),
        (
            lambda backend: setattr(
                backend,
                "app_ids",
                [
                    {"appId": 123456, "packageName": "com.example.wallet"},
                    {"appId": 999999, "packageName": "com.example.wallet"},
                ],
            ),
            "HONOR_APP_ID_AMBIGUOUS",
        ),
        (
            lambda backend: setattr(backend, "detail", _detail(package_name="com.other.wallet")),
            "HONOR_PACKAGE_MISMATCH",
        ),
        (
            lambda backend: setattr(backend, "detail", _detail(version_code=43)),
            "HONOR_VERSION_CONFLICT",
        ),
        (
            lambda backend: backend.detail.update({"languageInfo": []}),
            "HONOR_LOCALE_NOT_FOUND",
        ),
        (
            lambda backend: setattr(backend, "release", _release(audit_result=0)),
            "HONOR_REVIEW_CONFLICT",
        ),
        (
            lambda backend: setattr(backend, "release", _release(audit_result=3)),
            "HONOR_REVIEW_STATE_UNKNOWN",
        ),
        (
            lambda backend: setattr(backend, "release", _release(audit_result=4)),
            "HONOR_DRAFT_CONFLICT",
        ),
    ],
)
async def test_honor_verify_fails_closed_on_identity_version_locale_or_state(
    configure: Callable[[ReadBackend], None], expected_code: str
) -> None:
    backend = ReadBackend()
    configure(backend)
    adapter, http = _adapter(backend)
    try:
        with pytest.raises(HonorVendorError) as raised:
            await adapter.verify(target=_target())
    finally:
        await http.aclose()

    assert raised.value.code == expected_code
    rendered = str(raised.value)
    assert "must-never-leak" not in rendered
    assert "must-not-be-copied" not in rendered


@pytest.mark.asyncio
async def test_honor_rejected_current_update_can_be_corrected_at_same_target_version() -> None:
    backend = ReadBackend()
    backend.release = _release(audit_result=2, version_code=43)
    adapter, http = _adapter(backend)
    try:
        verified = await adapter.verify(target=_target(version_code=43))
    finally:
        await http.aclose()

    assert verified.app_id == "123456"


@pytest.mark.asyncio
async def test_honor_read_retries_transient_failures_at_most_twice() -> None:
    calls = 0
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.host == "iam.developer.honor.com":
            return httpx.Response(
                200,
                json={"access_token": "token", "expires_in": 3600, "token_type": "Bearer"},
            )
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"code": 503, "msg": "secret"})
        if calls == 2:
            raise httpx.ConnectError("secret", request=request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "ok",
                "data": [{"appId": 123456, "packageName": "com.example.wallet"}],
            },
        )

    adapter, http = _adapter(httpx.MockTransport(handler), sleeper=sleep)
    try:
        app_id = await adapter.client.get_app_id(package_name="com.example.wallet")
    finally:
        await http.aclose()

    assert app_id == 123456
    assert calls == 3
    assert sleeps == [1.0, 2.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, expected_code, exit_code",
    [
        (
            httpx.Response(302, headers={"location": "https://evil.example"}),
            "HONOR_REDIRECT",
            ExitCode.NETWORK,
        ),
        (
            httpx.Response(200, content=b"not-json"),
            "HONOR_RESPONSE_INVALID",
            ExitCode.VENDOR_REJECTION,
        ),
        (httpx.Response(200, json=[]), "HONOR_RESPONSE_INVALID", ExitCode.VENDOR_REJECTION),
        (
            httpx.Response(200, json={"code": 10003, "msg": "known-honor-secret"}),
            "HONOR_AUTHENTICATION_FAILED",
            ExitCode.AUTHENTICATION,
        ),
        (
            httpx.Response(200, json={"code": 20005, "msg": "known-honor-secret"}),
            "HONOR_APPLICATION_NOT_FOUND",
            ExitCode.VENDOR_REJECTION,
        ),
    ],
)
async def test_honor_read_errors_are_bounded_and_safe(
    response: httpx.Response, expected_code: str, exit_code: ExitCode
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "iam.developer.honor.com":
            return httpx.Response(
                200,
                json={"access_token": "token", "expires_in": 3600, "token_type": "Bearer"},
            )
        return response

    adapter, http = _adapter(httpx.MockTransport(handler))
    try:
        with pytest.raises(HonorVendorError) as raised:
            await adapter.client.get_app_id(package_name="com.example.wallet")
    finally:
        await http.aclose()

    assert raised.value.code == expected_code
    assert raised.value.exit_code is exit_code
    assert "known-honor-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_honor_oversized_response_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "iam.developer.honor.com":
            return httpx.Response(
                200,
                json={"access_token": "token", "expires_in": 3600, "token_type": "Bearer"},
            )
        return httpx.Response(200, content=b"{" + b"x" * (2 * 1024 * 1024 + 1))

    adapter, http = _adapter(httpx.MockTransport(handler))
    try:
        with pytest.raises(HonorVendorError) as raised:
            await adapter.client.get_app_id(package_name="com.example.wallet")
    finally:
        await http.aclose()

    assert raised.value.code == "HONOR_RESPONSE_INVALID"
