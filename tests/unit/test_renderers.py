from __future__ import annotations

import io
import json

from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode
from storehelper.domain.models import OperationResult, PublishStage
from storehelper.output.renderers import render_error, render_result


def _timeout() -> OperationResult:
    return OperationResult.failure(
        stage=PublishStage.TIMED_OUT,
        run_id="run-1",
        message="Huawei is still compiling.",
        resumable=True,
        vendor_code="204144727",
    )


def test_json_renderer_writes_exactly_one_document_to_stdout() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    render_result(_timeout(), output="json", stdout=stdout, stderr=stderr)

    payload, end = json.JSONDecoder().raw_decode(stdout.getvalue())
    assert payload["schema_version"] == 1
    assert stdout.getvalue()[end:].strip() == ""
    assert stderr.getvalue() == ""


def test_text_renderer_keeps_progress_channel_separate() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    render_result(_timeout(), output="text", stdout=stdout, stderr=stderr)

    assert "Huawei is still compiling" in stdout.getvalue()
    assert "storehelper resume run-1" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_error_renderer_redacts_at_output_boundary() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    error = StoreHelperError(
        "AUTH_FAILED",
        "Authorization: Bearer aaa.bbb.ccc",
        ExitCode.AUTHENTICATION,
    )

    render_error(error, output="json", stdout=stdout, stderr=stderr)

    payload = json.loads(stdout.getvalue())
    assert payload["code"] == "AUTH_FAILED"
    assert "aaa.bbb.ccc" not in stdout.getvalue() + stderr.getvalue()
