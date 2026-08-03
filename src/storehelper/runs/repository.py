"""Atomic persistence for redacted run receipts."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from platformdirs import user_state_path
from pydantic import ValidationError

from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode
from storehelper.runs.models import RunReceipt, RunState, migrate_receipt_payload
from storehelper.stores.models import StoreName

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class StateError(StoreHelperError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, ExitCode.LOCAL_STATE)


class RunRepository:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or user_state_path("storehelper", appauthor=False) / "runs"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with suppress(OSError):
            self.root.chmod(0o700)

    @staticmethod
    def _new_run_id(now: datetime) -> str:
        timestamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{timestamp}-{secrets.token_hex(4)}"

    def _path(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id):
            raise StateError("STATE_RUN_ID_INVALID", "Run ID contains invalid characters.")
        return self.root / f"{run_id}.json"

    def create(
        self,
        *,
        store: StoreName,
        app_alias: str,
        app_id: str,
        package_name: str,
        package_path: str,
        package_sha256: str,
        logical_name: str,
        operation_id: str | None = None,
        release_id: str | None = None,
        track: str | None = None,
        release_status: str | None = None,
        language: str,
        release_notes: str | None,
        submit: bool,
    ) -> RunReceipt:
        now = datetime.now(UTC)
        receipt = RunReceipt(
            run_id=self._new_run_id(now),
            created_at=now,
            updated_at=now,
            store=store,
            state=RunState.CREATED,
            app_alias=app_alias,
            app_id=app_id,
            package_name=package_name,
            package_path=package_path,
            package_sha256=package_sha256,
            logical_name=logical_name,
            operation_id=operation_id,
            release_id=release_id,
            track=track,
            release_status=release_status,
            language=language,
            release_notes=release_notes,
            submit=submit,
        )
        self.save(receipt)
        return receipt

    def save(self, receipt: RunReceipt) -> None:
        path = self._path(receipt.run_id)
        payload = receipt.model_dump_json(indent=2) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.root,
            prefix=f".{receipt.run_id}-",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            with suppress(OSError):
                path.chmod(0o600)
        except OSError:
            raise StateError("STATE_WRITE_FAILED", "Could not save the publishing run.") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary.exists():
                temporary.unlink()

    def get(self, run_id: str) -> RunReceipt:
        path = self._path(run_id)
        if not path.exists():
            raise StateError("STATE_NOT_FOUND", f"Publishing run not found: {run_id}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return RunReceipt.model_validate(migrate_receipt_payload(raw))
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError):
            raise StateError(
                "STATE_CORRUPT",
                f"Publishing run is unreadable or invalid: {run_id}",
            ) from None

    def list(self) -> list[RunReceipt]:
        receipts = [self.get(path.stem) for path in self.root.glob("*.json")]
        return sorted(receipts, key=lambda item: (item.created_at, item.run_id), reverse=True)

    def delete(self, run_id: str) -> bool:
        path = self._path(run_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError:
            raise StateError("STATE_DELETE_FAILED", f"Could not delete run: {run_id}") from None
        return True

    def find_resumable(
        self,
        store: str,
        app_id: str,
        package_sha256: str,
    ) -> RunReceipt | None:
        for receipt in self.list():
            if (
                receipt.resumable
                and receipt.store == store
                and receipt.app_id == app_id
                and receipt.package_sha256 == package_sha256
            ):
                return receipt
        return None
