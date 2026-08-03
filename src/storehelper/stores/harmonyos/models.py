"""Secret-safe temporary values for HarmonyOS OBS upload."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr


class OBSUploadTicket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: SecretStr
    method: Literal["PUT"] = "PUT"
    headers: dict[str, SecretStr]
    object_id: SecretStr
