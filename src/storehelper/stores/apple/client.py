"""Safe asynchronous App Store Connect JSON:API client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from storehelper.artifacts.models import AppleArtifactInfo
from storehelper.domain.exit_codes import ExitCode
from storehelper.stores.apple.auth import AppleAuth
from storehelper.stores.apple.errors import AppleVendorError, parse_apple_errors
from storehelper.stores.apple.models import AppleUploadReservation, validate_upload_plan
from storehelper.stores.models import StoreTarget, UploadedArtifact, VerifiedApplication

DEFAULT_API_BASE = "https://api.appstoreconnect.apple.com"
Sleeper = Callable[[float], Awaitable[None]]


class AppleClient:
    """Own authenticated JSON:API requests and verified target metadata."""

    def __init__(
        self,
        *,
        auth: AppleAuth,
        http: httpx.AsyncClient,
        api_base: str = DEFAULT_API_BASE,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._auth = auth
        self._http = http
        self._api_base = api_base.rstrip("/")
        self._sleeper = sleeper
        self._verified_versions: dict[str, str] = {}

    @staticmethod
    def _protocol_error(message: str) -> AppleVendorError:
        return AppleVendorError(
            "APPLE_RESPONSE_INVALID",
            message,
            ExitCode.VENDOR_REJECTION,
        )

    @staticmethod
    def _network_error(code: str, message: str) -> AppleVendorError:
        return AppleVendorError(code, message, ExitCode.NETWORK)

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("retry-after")
        if raw is not None:
            try:
                return min(60.0, max(0.0, float(raw)))
            except ValueError:
                pass
        return float(attempt)

    async def _send_authenticated(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        if not path.startswith("/v1/") or "://" in path:
            raise self._protocol_error("App Store Connect request path is invalid.")
        url = f"{self._api_base}{path}"
        base_headers = dict(kwargs.pop("headers", {}))
        refreshed = False
        force_refresh = False
        transient_attempts = 0
        while True:
            headers = dict(base_headers)
            headers.update(self._auth.headers(force_refresh=force_refresh))
            force_refresh = False
            try:
                response = await self._http.request(
                    method,
                    url,
                    headers=headers,
                    follow_redirects=False,
                    **kwargs,
                )
            except httpx.HTTPError:
                raise self._network_error(
                    "APPLE_NETWORK_ERROR",
                    "Could not reach App Store Connect.",
                ) from None
            if response.status_code == 401 and not refreshed:
                refreshed = True
                force_refresh = True
                continue
            if (
                response.status_code == 429 or response.status_code >= 500
            ) and transient_attempts < 2:
                transient_attempts += 1
                await self._sleeper(self._retry_delay(response, transient_attempts))
                continue
            return response

    async def request_json(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Mapping[str, object]:
        response = await self._send_authenticated(method, path, **kwargs)
        if response.is_redirect:
            raise self._network_error(
                "APPLE_REDIRECT",
                "App Store Connect returned an unexpected redirect.",
            )
        try:
            value = response.json()
        except (ValueError, UnicodeDecodeError):
            if response.is_error:
                raise parse_apple_errors(None, status_code=response.status_code) from None
            raise self._protocol_error("App Store Connect returned a non-JSON response.") from None
        if response.is_error:
            raise parse_apple_errors(value, status_code=response.status_code)
        if not isinstance(value, Mapping):
            raise self._protocol_error("App Store Connect returned an invalid JSON response.")
        return value

    async def request_empty(
        self,
        method: str,
        path: str,
        *,
        expected_status: int,
        **kwargs: Any,
    ) -> None:
        response = await self._send_authenticated(method, path, **kwargs)
        if response.is_redirect:
            raise self._network_error(
                "APPLE_REDIRECT",
                "App Store Connect returned an unexpected redirect.",
            )
        if response.is_error:
            try:
                value: object = response.json()
            except (ValueError, UnicodeDecodeError):
                value = None
            raise parse_apple_errors(value, status_code=response.status_code)
        if response.status_code != expected_status:
            raise self._protocol_error("App Store Connect returned an unexpected success status.")

    @staticmethod
    def _resource(
        payload: Mapping[str, object],
        *,
        resource_type: str,
        resource_id: str,
    ) -> Mapping[str, object]:
        data = payload.get("data")
        if (
            not isinstance(data, Mapping)
            or data.get("type") != resource_type
            or data.get("id") != resource_id
        ):
            raise AppleClient._protocol_error(
                f"App Store Connect returned an invalid {resource_type} resource."
            )
        return data

    async def verify_target(self, *, target: StoreTarget) -> VerifiedApplication:
        if target.release_id is None or target.platform != "IOS":
            raise AppleVendorError(
                "APPLE_TARGET_INVALID",
                "Apple target requires an iOS App Store version ID.",
                ExitCode.VENDOR_REJECTION,
            )
        app_id = quote(target.app_id, safe="")
        app_payload = await self.request_json(
            "GET",
            f"/v1/apps/{app_id}",
            params={"fields[apps]": "bundleId,name"},
        )
        app = self._resource(app_payload, resource_type="apps", resource_id=target.app_id)
        app_attributes = app.get("attributes")
        if not isinstance(app_attributes, Mapping):
            raise self._protocol_error("App Store Connect app attributes are missing.")
        if app_attributes.get("bundleId") != target.package_name:
            raise AppleVendorError(
                "APPLE_BUNDLE_MISMATCH",
                "The configured Apple bundle ID does not match the App Store Connect app.",
                ExitCode.VENDOR_REJECTION,
            )

        release_id = quote(target.release_id, safe="")
        version_payload = await self.request_json(
            "GET",
            f"/v1/appStoreVersions/{release_id}",
            params={
                "fields[appStoreVersions]": "platform,versionString,appStoreState",
                "include": "app",
            },
        )
        version = self._resource(
            version_payload,
            resource_type="appStoreVersions",
            resource_id=target.release_id,
        )
        attributes = version.get("attributes")
        if not isinstance(attributes, Mapping):
            raise self._protocol_error("App Store version attributes are missing.")
        if attributes.get("platform") != target.platform:
            raise AppleVendorError(
                "APPLE_PLATFORM_MISMATCH",
                "The configured Apple platform does not match the App Store version.",
                ExitCode.VENDOR_REJECTION,
            )
        version_string = attributes.get("versionString")
        if not isinstance(version_string, str) or not version_string.strip():
            raise self._protocol_error("App Store version string is missing.")
        relationships = version.get("relationships")
        app_relationship = relationships.get("app") if isinstance(relationships, Mapping) else None
        related_data = (
            app_relationship.get("data") if isinstance(app_relationship, Mapping) else None
        )
        if (
            not isinstance(related_data, Mapping)
            or related_data.get("type") != "apps"
            or related_data.get("id") != target.app_id
        ):
            raise AppleVendorError(
                "APPLE_VERSION_APP_MISMATCH",
                "The configured App Store version does not belong to the configured app.",
                ExitCode.VENDOR_REJECTION,
            )
        self._verified_versions[target.release_id] = version_string.strip()
        return VerifiedApplication(app_id=target.app_id, package_name=target.package_name)

    def verified_version_string(self, release_id: str) -> str | None:
        return self._verified_versions.get(release_id)

    @staticmethod
    def _resource_list(
        payload: Mapping[str, object],
        *,
        resource_type: str,
    ) -> list[Mapping[str, object]]:
        data = payload.get("data")
        if not isinstance(data, list):
            raise AppleClient._protocol_error(
                f"App Store Connect returned an invalid {resource_type} list."
            )
        resources: list[Mapping[str, object]] = []
        for resource in data:
            if not isinstance(resource, Mapping) or resource.get("type") != resource_type:
                raise AppleClient._protocol_error(
                    f"App Store Connect returned an invalid {resource_type} resource."
                )
            identifier = resource.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise AppleClient._protocol_error(
                    f"App Store Connect returned an invalid {resource_type} ID."
                )
            resources.append(resource)
        return resources

    @staticmethod
    def _matches_upload(resource: Mapping[str, object], artifact: AppleArtifactInfo) -> bool:
        attributes = resource.get("attributes")
        if not isinstance(attributes, Mapping):
            return False
        state = attributes.get("state")
        state_value = state.get("state") if isinstance(state, Mapping) else None
        return (
            attributes.get("cfBundleShortVersionString") == artifact.marketing_version
            and attributes.get("cfBundleVersion") == artifact.build_version
            and attributes.get("platform") == artifact.platform
            and state_value == "AWAITING_UPLOAD"
        )

    async def _find_or_create_upload(
        self,
        *,
        target: StoreTarget,
        artifact: AppleArtifactInfo,
    ) -> tuple[str, bool]:
        app_id = quote(target.app_id, safe="")
        payload = await self.request_json(
            "GET",
            f"/v1/apps/{app_id}/buildUploads",
            params={
                "filter[cfBundleShortVersionString]": artifact.marketing_version,
                "filter[cfBundleVersion]": artifact.build_version,
                "filter[platform]": artifact.platform,
                "filter[state]": "AWAITING_UPLOAD",
                "fields[buildUploads]": (
                    "cfBundleShortVersionString,cfBundleVersion,platform,state"
                ),
                "limit": 200,
            },
        )
        resources = self._resource_list(payload, resource_type="buildUploads")
        matches = [resource for resource in resources if self._matches_upload(resource, artifact)]
        if len(matches) > 1:
            raise AppleVendorError(
                "APPLE_UPLOAD_RECONCILIATION_AMBIGUOUS",
                "Multiple matching Apple build uploads are awaiting the same IPA.",
                ExitCode.VENDOR_REJECTION,
            )
        if len(matches) == 1:
            return str(matches[0]["id"]), False
        created = await self.request_json(
            "POST",
            "/v1/buildUploads",
            json={
                "data": {
                    "type": "buildUploads",
                    "attributes": {
                        "cfBundleShortVersionString": artifact.marketing_version,
                        "cfBundleVersion": artifact.build_version,
                        "platform": artifact.platform,
                    },
                    "relationships": {"app": {"data": {"type": "apps", "id": target.app_id}}},
                }
            },
        )
        created_data = created.get("data")
        identifier = created_data.get("id") if isinstance(created_data, Mapping) else None
        resource = self._resource(
            created,
            resource_type="buildUploads",
            resource_id=identifier if isinstance(identifier, str) else "",
        )
        return str(resource["id"]), True

    @staticmethod
    def _matches_file(resource: Mapping[str, object], artifact: AppleArtifactInfo) -> bool:
        attributes = resource.get("attributes")
        return isinstance(attributes, Mapping) and (
            attributes.get("assetType") == "ASSET"
            and attributes.get("fileName") == artifact.logical_name
            and attributes.get("fileSize") == artifact.size
            and attributes.get("uti") == "com.apple.ipa"
        )

    async def _create_file(
        self,
        *,
        upload_id: str,
        artifact: AppleArtifactInfo,
    ) -> Mapping[str, object]:
        payload = await self.request_json(
            "POST",
            "/v1/buildUploadFiles",
            json={
                "data": {
                    "type": "buildUploadFiles",
                    "attributes": {
                        "assetType": "ASSET",
                        "fileName": artifact.logical_name,
                        "fileSize": artifact.size,
                        "uti": "com.apple.ipa",
                    },
                    "relationships": {
                        "buildUpload": {"data": {"type": "buildUploads", "id": upload_id}}
                    },
                }
            },
        )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise self._protocol_error("Apple build upload file response is invalid.")
        identifier = data.get("id")
        return self._resource(
            payload,
            resource_type="buildUploadFiles",
            resource_id=identifier if isinstance(identifier, str) else "",
        )

    async def _find_or_create_file(
        self,
        *,
        upload_id: str,
        upload_created: bool,
        artifact: AppleArtifactInfo,
    ) -> Mapping[str, object]:
        if upload_created:
            return await self._create_file(upload_id=upload_id, artifact=artifact)
        payload = await self.request_json(
            "GET",
            f"/v1/buildUploads/{quote(upload_id, safe='')}/buildUploadFiles",
            params={
                "fields[buildUploadFiles]": ("assetType,fileName,fileSize,uti,uploadOperations"),
                "limit": 200,
            },
        )
        resources = self._resource_list(payload, resource_type="buildUploadFiles")
        matches = [resource for resource in resources if self._matches_file(resource, artifact)]
        if len(matches) > 1:
            raise AppleVendorError(
                "APPLE_UPLOAD_RECONCILIATION_AMBIGUOUS",
                "Multiple matching Apple upload file reservations were returned.",
                ExitCode.VENDOR_REJECTION,
            )
        if len(matches) == 1:
            return matches[0]
        return await self._create_file(upload_id=upload_id, artifact=artifact)

    async def reserve_upload(
        self,
        *,
        target: StoreTarget,
        artifact: AppleArtifactInfo,
    ) -> AppleUploadReservation:
        upload_id, created = await self._find_or_create_upload(target=target, artifact=artifact)
        resource = await self._find_or_create_file(
            upload_id=upload_id,
            upload_created=created,
            artifact=artifact,
        )
        identifier = resource.get("id")
        attributes = resource.get("attributes")
        raw_operations = (
            attributes.get("uploadOperations") if isinstance(attributes, Mapping) else None
        )
        if not isinstance(identifier, str) or not isinstance(raw_operations, list):
            raise self._protocol_error("Apple upload file reservation is incomplete.")
        if not all(isinstance(operation, Mapping) for operation in raw_operations):
            raise self._protocol_error("Apple upload file operations are invalid.")
        operations = validate_upload_plan(
            raw_operations,
            artifact.size,
            datetime.now(UTC),
        )
        return AppleUploadReservation(
            upload_id=upload_id,
            file_id=identifier,
            operations=operations,
        )

    @staticmethod
    async def _range_chunks(
        artifact: AppleArtifactInfo,
        *,
        offset: int,
        length: int,
    ) -> AsyncIterator[bytes]:
        remaining = length
        try:
            with artifact.path.open("rb") as stream:
                stream.seek(offset)
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise OSError("IPA ended before the reserved range")
                    remaining -= len(chunk)
                    yield chunk
        except OSError:
            raise AppleVendorError(
                "APPLE_DELIVERY_FAILED",
                "Could not read the IPA range reserved by App Store Connect.",
                ExitCode.NETWORK,
            ) from None

    async def transfer_upload(
        self,
        *,
        artifact: AppleArtifactInfo,
        reservation: AppleUploadReservation,
    ) -> None:
        for operation in reservation.operations:
            if operation.delivered:
                continue
            headers = {name: value.get_secret_value() for name, value in operation.headers.items()}
            try:
                response = await self._http.request(
                    operation.method,
                    operation.url.get_secret_value(),
                    headers=headers,
                    content=self._range_chunks(
                        artifact,
                        offset=operation.offset,
                        length=operation.length,
                    ),
                    follow_redirects=False,
                )
            except (httpx.HTTPError, AppleVendorError):
                raise AppleVendorError(
                    "APPLE_DELIVERY_FAILED",
                    "Apple delivery storage did not accept an IPA range.",
                    ExitCode.NETWORK,
                ) from None
            if response.is_redirect or response.is_error:
                raise AppleVendorError(
                    "APPLE_DELIVERY_FAILED",
                    "Apple delivery storage did not accept an IPA range.",
                    ExitCode.NETWORK,
                )

    async def commit_upload_file(
        self,
        *,
        reservation: AppleUploadReservation,
        artifact: AppleArtifactInfo,
    ) -> None:
        file_id = quote(reservation.file_id, safe="")
        await self.request_json(
            "PATCH",
            f"/v1/buildUploadFiles/{file_id}",
            json={
                "data": {
                    "type": "buildUploadFiles",
                    "id": reservation.file_id,
                    "attributes": {
                        "sourceFileChecksums": {
                            "file": {"algorithm": "SHA_256", "hash": artifact.sha256}
                        },
                        "uploaded": True,
                    },
                }
            },
        )

    async def upload_ipa(
        self,
        *,
        target: StoreTarget,
        artifact: AppleArtifactInfo,
    ) -> UploadedArtifact:
        reservation = await self.reserve_upload(target=target, artifact=artifact)
        await self.transfer_upload(artifact=artifact, reservation=reservation)
        await self.commit_upload_file(reservation=reservation, artifact=artifact)
        return UploadedArtifact(artifact_id=reservation.upload_id)

    async def build_upload_status(self, upload_id: str) -> Mapping[str, object]:
        return await self.request_json(
            "GET",
            f"/v1/buildUploads/{quote(upload_id, safe='')}",
            params={
                "fields[buildUploads]": "state,build",
                "include": "build",
            },
        )

    async def attach_build(self, *, release_id: str, build_id: str) -> None:
        await self.request_empty(
            "PATCH",
            f"/v1/appStoreVersions/{quote(release_id, safe='')}/relationships/build",
            expected_status=204,
            json={"data": {"type": "builds", "id": build_id}},
        )

    async def update_whats_new(
        self,
        *,
        release_id: str,
        language: str,
        release_notes: str,
    ) -> None:
        payload = await self.request_json(
            "GET",
            f"/v1/appStoreVersions/{quote(release_id, safe='')}/appStoreVersionLocalizations",
            params={
                "filter[locale]": language,
                "fields[appStoreVersionLocalizations]": "locale",
                "limit": 200,
            },
        )
        resources = self._resource_list(
            payload,
            resource_type="appStoreVersionLocalizations",
        )
        matches: list[Mapping[str, object]] = []
        for resource in resources:
            attributes = resource.get("attributes")
            if isinstance(attributes, Mapping) and attributes.get("locale") == language:
                matches.append(resource)
        if len(matches) != 1:
            raise AppleVendorError(
                "APPLE_LOCALIZATION_NOT_UNIQUE",
                "The configured Apple locale must match exactly one version localization.",
                ExitCode.VENDOR_REJECTION,
            )
        localization_id = str(matches[0]["id"])
        await self.request_json(
            "PATCH",
            f"/v1/appStoreVersionLocalizations/{quote(localization_id, safe='')}",
            json={
                "data": {
                    "type": "appStoreVersionLocalizations",
                    "id": localization_id,
                    "attributes": {"whatsNew": release_notes},
                }
            },
        )

    async def prepare_release(
        self,
        *,
        target: StoreTarget,
        build_id: str,
        release_notes: str | None,
    ) -> None:
        if target.release_id is None:
            raise AppleVendorError(
                "APPLE_TARGET_INVALID",
                "Apple target requires an App Store version ID.",
                ExitCode.VENDOR_REJECTION,
            )
        await self.attach_build(release_id=target.release_id, build_id=build_id)
        if release_notes is not None:
            await self.update_whats_new(
                release_id=target.release_id,
                language=target.language,
                release_notes=release_notes,
            )

    @staticmethod
    def _matches_draft(resource: Mapping[str, object], platform: str) -> bool:
        attributes = resource.get("attributes")
        return isinstance(attributes, Mapping) and (
            attributes.get("platform") == platform and attributes.get("state") == "READY_FOR_REVIEW"
        )

    async def _find_or_create_review_submission(self, target: StoreTarget) -> str:
        if target.platform is None:
            raise AppleVendorError(
                "APPLE_TARGET_INVALID",
                "Apple target requires a platform.",
                ExitCode.VENDOR_REJECTION,
            )
        payload = await self.request_json(
            "GET",
            f"/v1/apps/{quote(target.app_id, safe='')}/reviewSubmissions",
            params={
                "filter[platform]": target.platform,
                "filter[state]": "READY_FOR_REVIEW",
                "fields[reviewSubmissions]": "platform,state",
                "limit": 200,
            },
        )
        resources = self._resource_list(payload, resource_type="reviewSubmissions")
        matches = [
            resource for resource in resources if self._matches_draft(resource, target.platform)
        ]
        if len(matches) > 1:
            raise AppleVendorError(
                "APPLE_REVIEW_SUBMISSION_AMBIGUOUS",
                "Multiple reusable Apple review submissions were returned.",
                ExitCode.VENDOR_REJECTION,
            )
        if len(matches) == 1:
            return str(matches[0]["id"])
        created = await self.request_json(
            "POST",
            "/v1/reviewSubmissions",
            json={
                "data": {
                    "type": "reviewSubmissions",
                    "attributes": {"platform": target.platform},
                    "relationships": {"app": {"data": {"type": "apps", "id": target.app_id}}},
                }
            },
        )
        data = created.get("data")
        identifier = data.get("id") if isinstance(data, Mapping) else None
        resource = self._resource(
            created,
            resource_type="reviewSubmissions",
            resource_id=identifier if isinstance(identifier, str) else "",
        )
        return str(resource["id"])

    @staticmethod
    def _item_version(resource: Mapping[str, object]) -> str | None:
        relationships = resource.get("relationships")
        version = (
            relationships.get("appStoreVersion") if isinstance(relationships, Mapping) else None
        )
        data = version.get("data") if isinstance(version, Mapping) else None
        if not isinstance(data, Mapping) or data.get("type") != "appStoreVersions":
            return None
        identifier = data.get("id")
        return identifier if isinstance(identifier, str) and identifier else None

    async def _ensure_version_item(
        self,
        *,
        submission_id: str,
        release_id: str,
    ) -> None:
        payload = await self.request_json(
            "GET",
            f"/v1/reviewSubmissions/{quote(submission_id, safe='')}/items",
            params={
                "fields[reviewSubmissionItems]": "appStoreVersion,state",
                "include": "appStoreVersion",
                "limit": 200,
            },
        )
        resources = self._resource_list(payload, resource_type="reviewSubmissionItems")
        versions = [version for resource in resources if (version := self._item_version(resource))]
        wrong_versions = [version for version in versions if version != release_id]
        if wrong_versions:
            raise AppleVendorError(
                "APPLE_REVIEW_ITEM_VERSION_MISMATCH",
                "The reusable Apple review submission contains a different App Store version.",
                ExitCode.VENDOR_REJECTION,
            )
        if versions.count(release_id) > 1:
            raise AppleVendorError(
                "APPLE_REVIEW_ITEM_AMBIGUOUS",
                "The Apple review submission contains duplicate version items.",
                ExitCode.VENDOR_REJECTION,
            )
        if release_id in versions:
            return
        await self.request_json(
            "POST",
            "/v1/reviewSubmissionItems",
            json={
                "data": {
                    "type": "reviewSubmissionItems",
                    "relationships": {
                        "reviewSubmission": {
                            "data": {
                                "type": "reviewSubmissions",
                                "id": submission_id,
                            }
                        },
                        "appStoreVersion": {
                            "data": {
                                "type": "appStoreVersions",
                                "id": release_id,
                            }
                        },
                    },
                }
            },
        )

    async def submit_for_review(self, *, target: StoreTarget) -> str:
        if target.release_id is None:
            raise AppleVendorError(
                "APPLE_TARGET_INVALID",
                "Apple target requires an App Store version ID.",
                ExitCode.VENDOR_REJECTION,
            )
        submission_id = await self._find_or_create_review_submission(target)
        await self._ensure_version_item(
            submission_id=submission_id,
            release_id=target.release_id,
        )
        await self.request_json(
            "PATCH",
            f"/v1/reviewSubmissions/{quote(submission_id, safe='')}",
            json={
                "data": {
                    "type": "reviewSubmissions",
                    "id": submission_id,
                    "attributes": {"submitted": True},
                }
            },
        )
        return submission_id

    async def app_store_version_status(
        self,
        *,
        release_id: str,
    ) -> Mapping[str, object]:
        return await self.request_json(
            "GET",
            f"/v1/appStoreVersions/{quote(release_id, safe='')}",
            params={"fields[appStoreVersions]": "appStoreState"},
        )
