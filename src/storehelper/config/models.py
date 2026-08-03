"""Strict schema version 1 project configuration models."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmptyString = Annotated[str, Field(min_length=1)]
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
    package_name: NonEmptyString
    credential_profile: NonEmptyString
    language: NonEmptyString = "zh-CN"

    @field_validator("package_name")
    @classmethod
    def validate_package_name(cls, value: str) -> str:
        if not _PACKAGE_NAME.fullmatch(value):
            raise ValueError("must be a dotted HarmonyOS package name")
        return value


class AppleStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    app_id: NonEmptyString
    bundle_id: NonEmptyString
    app_store_version_id: NonEmptyString
    credential_profile: NonEmptyString
    platform: Literal["IOS"] = "IOS"
    language: NonEmptyString = "en-US"

    @field_validator("bundle_id")
    @classmethod
    def validate_bundle_id(cls, value: str) -> str:
        if not _PACKAGE_NAME.fullmatch(value):
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


class StoreConfigs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    huawei: HuaweiStoreConfig | None = None
    harmonyos: HarmonyOSStoreConfig | None = None
    apple: AppleStoreConfig | None = None
    google_play: GooglePlayStoreConfig | None = None

    @model_validator(mode="after")
    def require_one_store(self) -> StoreConfigs:
        if not any((self.huawei, self.harmonyos, self.apple, self.google_play)):
            raise ValueError("at least one store must be configured")
        return self


class ApplicationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    package_name: NonEmptyString
    stores: StoreConfigs

    @field_validator("package_name")
    @classmethod
    def validate_package_name(cls, value: str) -> str:
        if not _PACKAGE_NAME.fullmatch(value):
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
