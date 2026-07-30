"""Strict schema version 1 project configuration models."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NonEmptyString = Annotated[str, Field(min_length=1)]
_APP_ALIAS = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_PACKAGE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")


class HuaweiStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    app_id: NonEmptyString
    credential_profile: NonEmptyString
    language: NonEmptyString = "zh-CN"


class StoreConfigs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    huawei: HuaweiStoreConfig


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
