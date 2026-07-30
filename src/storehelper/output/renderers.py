"""Text and machine-readable output boundaries with final redaction."""

from __future__ import annotations

import json
import sys
from typing import Literal, TextIO

from storehelper.domain.errors import StoreHelperError, redact
from storehelper.domain.models import OperationResult

OutputFormat = Literal["text", "json"]


def render_result(
    result: OperationResult,
    *,
    output: OutputFormat,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    del stderr
    target = stdout or sys.stdout
    if output == "json":
        payload = result.model_dump(mode="json")
        target.write(redact(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
        target.write("\n")
        return
    prefix = "OK" if result.ok else "ERROR"
    target.write(f"{prefix} [{result.stage.value}] {redact(result.message)}\n")
    if result.run_id:
        target.write(f"Run: {result.run_id}\n")
    if result.next_action:
        target.write(f"Next: {redact(result.next_action.command)}\n")


def render_error(
    error: StoreHelperError,
    *,
    output: OutputFormat,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    safe_message = redact(error.message)
    if output == "json":
        target = stdout or sys.stdout
        target.write(
            json.dumps(
                {"ok": False, "code": error.code, "message": safe_message},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        target.write("\n")
        return
    target = stderr or sys.stderr
    target.write(f"ERROR [{error.code}] {safe_message}\n")
