"""Strict schema version 1 project configuration models."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

NonEmptyString = Annotated[str, Field(min_length=1)]
StrictPositiveInt = Annotated[int, Field(strict=True, gt=0)]
_APP_ALIAS = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_PACKAGE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
_GOOGLE_TRACK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_BCP47_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")


class HuaweiStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    app_id: NonEmptyString
    credential_profile: NonEmptyString
    language: NonEmptyString = "zh-CN"


class HarmonyOSStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    app_id: NonEmptyString
    package_name: NonEmptyString | None = None
    credential_profile: NonEmptyString
    language: NonEmptyString = "zh-CN"

    @field_validator("package_name")
    @classmethod
    def validate_package_name(cls, value: str | None) -> str | None:
        if value is not None and not _PACKAGE_NAME.fullmatch(value):
            raise ValueError("must be a dotted HarmonyOS package name")
        return value


class AppleStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    app_id: NonEmptyString
    bundle_id: NonEmptyString | None = None
    app_store_version_id: NonEmptyString
    credential_profile: NonEmptyString
    platform: Literal["IOS"] = "IOS"
    language: NonEmptyString = "en-US"

    @field_validator("bundle_id")
    @classmethod
    def validate_bundle_id(cls, value: str | None) -> str | None:
        if value is not None and not _PACKAGE_NAME.fullmatch(value):
            raise ValueError("must be a dotted Apple bundle ID")
        return value


class GooglePlayStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    credential_profile: NonEmptyString
    track: NonEmptyString
    release_status: Literal["draft", "completed"]
    language: NonEmptyString = "en-US"

    @field_validator("track")
    @classmethod
    def validate_track(cls, value: str) -> str:
        if not _GOOGLE_TRACK.fullmatch(value):
            raise ValueError("must be a safe Google Play track identifier")
        return value

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if not _BCP47_LANGUAGE.fullmatch(value):
            raise ValueError("must be a BCP-47 language tag")
        return value


class XiaomiStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    credential_profile: NonEmptyString
    app_name: NonEmptyString
    icon: Path
    privacy_url: NonEmptyString
    language: NonEmptyString = "zh-CN"

    @field_validator("icon", mode="before")
    @classmethod
    def resolve_icon(cls, value: object, info: ValidationInfo) -> Path:
        if not str(value).strip():
            raise ValueError("must not be empty")
        value = Path(str(value))
        config_dir = (info.context or {}).get("config_dir")
        if not value.is_absolute() and isinstance(config_dir, Path):
            value = config_dir / value
        return value.expanduser().resolve(strict=False)

    @field_validator("privacy_url")
    @classmethod
    def validate_privacy_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("must be an HTTPS URL without embedded credentials")
        return value

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if not _BCP47_LANGUAGE.fullmatch(value):
            raise ValueError("must be a BCP-47 language tag")
        return value


class OppoStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    credential_profile: NonEmptyString
    version_code: StrictPositiveInt | None = None
    language: NonEmptyString = "zh-CN"

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if not _BCP47_LANGUAGE.fullmatch(value):
            raise ValueError("must be a BCP-47 language tag")
        return value


class VivoStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    credential_profile: NonEmptyString
    version_code: StrictPositiveInt | None = None
    language: NonEmptyString = "zh-CN"

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if not _BCP47_LANGUAGE.fullmatch(value):
            raise ValueError("must be a BCP-47 language tag")
        return value


class HonorStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    credential_profile: NonEmptyString
    version_code: StrictPositiveInt | None = None
    language: NonEmptyString = "zh-CN"

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if not _BCP47_LANGUAGE.fullmatch(value):
            raise ValueError("must be a BCP-47 language tag")
        return value


class StoreConfigs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    huawei: HuaweiStoreConfig | None = None
    harmonyos: HarmonyOSStoreConfig | None = None
    apple: AppleStoreConfig | None = None
    google_play: GooglePlayStoreConfig | None = None
    xiaomi: XiaomiStoreConfig | None = None
    oppo: OppoStoreConfig | None = None
    vivo: VivoStoreConfig | None = None
    honor: HonorStoreConfig | None = None

    @model_validator(mode="after")
    def require_one_store(self) -> StoreConfigs:
        if not any(
            (
                self.huawei,
                self.harmonyos,
                self.apple,
                self.google_play,
                self.xiaomi,
                self.oppo,
                self.vivo,
                self.honor,
            )
        ):
            raise ValueError("at least one store must be configured")
        return self


class ApplicationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    package_name: NonEmptyString | None = None
    stores: StoreConfigs

    @field_validator("package_name")
    @classmethod
    def validate_package_name(cls, value: str | None) -> str | None:
        if value is not None and not _PACKAGE_NAME.fullmatch(value):
            raise ValueError("must be a dotted Android package name")
        return value


class StoreHelperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    apps: dict[str, ApplicationConfig] = Field(min_length=1)

    @field_validator("apps")
    @classmethod
    def validate_aliases(cls, apps: dict[str, ApplicationConfig]) -> dict[str, ApplicationConfig]:
        invalid = [alias for alias in apps if not _APP_ALIAS.fullmatch(alias)]
        if invalid:
            raise ValueError(f"invalid application alias: {invalid[0]}")
        return apps
