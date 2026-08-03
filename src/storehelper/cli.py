"""StoreHelper command-line interface."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Annotated, Any, NoReturn

import httpx
import typer

from storehelper import __version__
from storehelper.artifacts.models import ArtifactInfo
from storehelper.commands.config import initialize, validate
from storehelper.commands.credentials import delete_profile, import_profile, list_profiles
from storehelper.config.loader import load_config, resolve_store_target, select_application
from storehelper.config.models import ApplicationConfig
from storehelper.credentials.providers import CredentialProvider, KeyringStore, SystemKeyring
from storehelper.domain.errors import StoreHelperError
from storehelper.domain.exit_codes import ExitCode
from storehelper.domain.models import OperationResult, PublishRequest, PublishStage
from storehelper.output.renderers import OutputFormat, render_error, render_result
from storehelper.publishing.service import Publisher, PublishingError
from storehelper.runs.repository import RunRepository
from storehelper.stores.base import StoreAdapter
from storehelper.stores.huawei.adapter import HuaweiAndroidAdapter
from storehelper.stores.huawei.auth import HuaweiAuth
from storehelper.stores.huawei.client import HuaweiClient
from storehelper.stores.huawei.package import validate_package
from storehelper.stores.models import (
    CredentialKind,
    ProcessingStatus,
    ReviewStatus,
    StoreCapabilities,
    StoreName,
    UploadedArtifact,
    VerifiedApplication,
)

app = typer.Typer(
    name="storehelper",
    help="Local-first app store publishing for developers, CI/CD, and AI agents.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Validate project configuration.")
credentials_app = typer.Typer(help="Manage Huawei Service Account profiles securely.")
runs_app = typer.Typer(help="Inspect and delete redacted local publishing runs.")
app.add_typer(config_app, name="config")
app.add_typer(credentials_app, name="credentials")
app.add_typer(runs_app, name="runs")

KEYRING: KeyringStore = SystemKeyring()
RUNS_ROOT: Path | None = None
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)(s|m|h)?$")
_HUAWEI_CAPABILITIES = StoreCapabilities(
    credential_kind=CredentialKind.HUAWEI_SERVICE_ACCOUNT,
    artifact_suffixes=(".apk", ".aab"),
    requires_processing_poll=True,
    requires_release_notes=True,
    supports_review_status=True,
)


def _output(value: str) -> OutputFormat:
    if value not in ("text", "json"):
        raise typer.BadParameter("must be text or json", param_hint="--output")
    return value  # type: ignore[return-value]


def _abort(error: StoreHelperError, output: OutputFormat) -> NoReturn:
    render_error(error, output=output)
    raise typer.Exit(code=int(error.exit_code))


def _duration(value: str) -> float:
    match = _DURATION.fullmatch(value.strip().lower())
    if match is None:
        raise typer.BadParameter("must be a duration such as 15s, 10m, or 1h")
    amount = float(match.group(1))
    factor = {None: 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}[match.group(2)]
    return amount * factor


def _interactive(output: OutputFormat) -> bool:
    return output == "text" and sys.stdin.isatty()


def _result_exit_code(result: OperationResult) -> int:
    if result.ok:
        return int(ExitCode.SUCCESS)
    if result.stage is PublishStage.TIMED_OUT:
        return int(ExitCode.RESUMABLE_TIMEOUT)
    if result.stage is PublishStage.INTERRUPTED:
        return int(ExitCode.INTERRUPTED)
    return int(ExitCode.VENDOR_REJECTION)


class _NoNetworkAdapter:
    """A fail-fast adapter used to prove dry-run never performs I/O."""

    @staticmethod
    def _failed() -> NoReturn:
        raise AssertionError("dry-run attempted a network operation")

    async def verify(self, *, app_id: str, package_name: str) -> VerifiedApplication:
        self._failed()

    async def upload(self, *, app_id: str, artifact: ArtifactInfo) -> UploadedArtifact:
        self._failed()

    async def processing_status(
        self,
        *,
        app_id: str,
        artifact_id: str,
    ) -> ProcessingStatus:
        self._failed()

    async def update_release_notes(
        self,
        *,
        app_id: str,
        language: str,
        release_notes: str,
    ) -> None:
        self._failed()

    async def submit(self, *, app_id: str) -> str:
        self._failed()

    async def review_status(self, *, app_id: str) -> ReviewStatus:
        self._failed()


def _publisher(
    *,
    adapter: StoreAdapter,
    application: ApplicationConfig,
) -> Publisher:
    target = resolve_store_target(application, StoreName.HUAWEI)
    return Publisher(
        adapter=adapter,
        repository=RunRepository(RUNS_ROOT),
        target=target,
        validator=validate_package,
        capabilities=_HUAWEI_CAPABILITIES,
    )


async def _publish_operation(
    *,
    request: PublishRequest,
    config_path: Path,
    app_alias: str | None,
    interactive: bool,
) -> OperationResult:
    config = load_config(config_path)
    selected_alias, application = select_application(config, app_alias)
    if not request.app_alias:
        request = request.model_copy(update={"app_alias": selected_alias})
    if request.dry_run:
        return await _publisher(adapter=_NoNetworkAdapter(), application=application).publish(
            request
        )
    huawei = application.stores.huawei
    assert huawei is not None
    account = CredentialProvider(KEYRING).resolve(
        huawei.credential_profile,
        interactive=interactive,
    )
    timeout = httpx.Timeout(connect=10.0, read=60.0, write=600.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        adapter = HuaweiAndroidAdapter(HuaweiClient(auth=HuaweiAuth(account), http=http))
        return await _publisher(adapter=adapter, application=application).publish(request)


async def _resume_operation(
    *,
    run_id: str,
    config_path: Path,
    app_alias: str | None,
    interactive: bool,
    poll_interval: float,
    wait_timeout: float,
) -> OperationResult:
    config = load_config(config_path)
    _, application = select_application(config, app_alias)
    huawei = application.stores.huawei
    assert huawei is not None
    account = CredentialProvider(KEYRING).resolve(
        huawei.credential_profile,
        interactive=interactive,
    )
    timeout = httpx.Timeout(connect=10.0, read=60.0, write=600.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        adapter = HuaweiAndroidAdapter(HuaweiClient(auth=HuaweiAuth(account), http=http))
        publisher = _publisher(adapter=adapter, application=application)
        return await publisher.resume(
            run_id,
            poll_interval=poll_interval,
            wait_timeout=wait_timeout,
        )


async def _status_operation(
    *,
    config_path: Path,
    app_alias: str | None,
    interactive: bool,
) -> OperationResult:
    config = load_config(config_path)
    _, application = select_application(config, app_alias)
    huawei = application.stores.huawei
    assert huawei is not None
    account = CredentialProvider(KEYRING).resolve(
        huawei.credential_profile,
        interactive=interactive,
    )
    async with httpx.AsyncClient(timeout=30.0) as http:
        adapter = HuaweiAndroidAdapter(HuaweiClient(auth=HuaweiAuth(account), http=http))
        return await _publisher(adapter=adapter, application=application).status()


async def _verify_credentials_operation(
    *,
    config_path: Path,
    app_alias: str | None,
    profile: str | None,
    interactive: bool,
) -> OperationResult:
    config = load_config(config_path)
    _, application = select_application(config, app_alias)
    huawei = application.stores.huawei
    assert huawei is not None
    account = CredentialProvider(KEYRING).resolve(
        profile or huawei.credential_profile,
        interactive=interactive,
    )
    async with httpx.AsyncClient(timeout=30.0) as http:
        client = HuaweiClient(auth=HuaweiAuth(account), http=http)
        await client.verify_app(
            app_id=huawei.app_id,
            package_name=application.package_name,
        )
    return OperationResult.success(
        stage=PublishStage.APP_VERIFIED,
        run_id=None,
        message="Huawei credentials and configured application were verified.",
    )


def _run_operation(
    coro: Coroutine[Any, Any, OperationResult],
    output: OutputFormat,
) -> None:
    try:
        result = asyncio.run(coro)
    except StoreHelperError as error:
        _abort(error, output)
    except KeyboardInterrupt:
        raise typer.Exit(code=int(ExitCode.INTERRUPTED)) from None
    render_result(result, output=output)
    code = _result_exit_code(result)
    if code:
        raise typer.Exit(code=code)


@app.command()
def version() -> None:
    """Print the installed StoreHelper version."""

    typer.echo(__version__)


@app.command("init")
def init_command(
    config: Annotated[Path, typer.Option("--config")] = Path("storehelper.yaml"),
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    """Create a secret-free example configuration."""

    output_format = _output(output)
    try:
        initialize(config)
    except StoreHelperError as error:
        _abort(error, output_format)
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "config": str(config)}, separators=(",", ":")))
    else:
        typer.echo(f"Created {config}")


@config_app.command("validate")
def config_validate(
    config: Annotated[Path, typer.Option("--config")] = Path("storehelper.yaml"),
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    """Validate configuration schema and reject embedded secrets."""

    output_format = _output(output)
    try:
        validate(config)
    except StoreHelperError as error:
        _abort(error, output_format)
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "valid": True}, separators=(",", ":")))
    else:
        typer.echo("Configuration is valid.")


@credentials_app.command("import")
def credentials_import(
    profile: Annotated[str, typer.Option("--profile")],
    file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)],
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    """Import Service Account JSON into the operating-system keyring."""

    output_format = _output(output)
    try:
        imported = import_profile(KEYRING, profile, file)
    except StoreHelperError as error:
        _abort(error, output_format)
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "profile": imported}, separators=(",", ":")))
    else:
        typer.echo(f"Imported Huawei credential profile: {imported}")


@credentials_app.command("list")
def credentials_list(output: Annotated[str, typer.Option("--output")] = "text") -> None:
    """List profile names without reading secret values."""

    output_format = _output(output)
    try:
        profiles = list_profiles(KEYRING)
    except StoreHelperError as error:
        _abort(error, output_format)
    if output_format == "json":
        typer.echo(json.dumps({"profiles": profiles}, separators=(",", ":")))
    else:
        typer.echo("\n".join(profiles) if profiles else "No credential profiles.")


@credentials_app.command("delete")
def credentials_delete(
    profile: Annotated[str, typer.Option("--profile")],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    """Delete one credential profile after confirmation."""

    output_format = _output(output)
    if not yes and (
        output_format == "json" or not typer.confirm(f"Delete credential profile {profile}?")
    ):
        raise typer.Exit(code=2)
    try:
        delete_profile(KEYRING, profile)
    except StoreHelperError as error:
        _abort(error, output_format)
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "profile": profile}, separators=(",", ":")))
    else:
        typer.echo(f"Deleted Huawei credential profile: {profile}")


@credentials_app.command("verify")
def credentials_verify(
    app_alias: Annotated[str | None, typer.Option("--app")] = None,
    profile: Annotated[str | None, typer.Option("--profile")] = None,
    output: Annotated[str, typer.Option("--output")] = "text",
    config: Annotated[Path, typer.Option("--config")] = Path("storehelper.yaml"),
) -> None:
    """Verify credentials and app access using a read-only Huawei request."""

    output_format = _output(output)
    _run_operation(
        _verify_credentials_operation(
            config_path=config,
            app_alias=app_alias,
            profile=profile,
            interactive=_interactive(output_format),
        ),
        output_format,
    )


@app.command()
def publish(
    file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)],
    app_alias: Annotated[str | None, typer.Option("--app")] = None,
    store: Annotated[str, typer.Option("--store")] = "huawei",
    release_notes: Annotated[str | None, typer.Option("--release-notes")] = None,
    release_notes_file: Annotated[
        Path | None,
        typer.Option("--release-notes-file", exists=True, dir_okay=False),
    ] = None,
    no_submit: Annotated[bool, typer.Option("--no-submit")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    poll_interval: Annotated[str, typer.Option("--poll-interval")] = "15s",
    wait_timeout: Annotated[str, typer.Option("--wait-timeout")] = "10m",
    output: Annotated[str, typer.Option("--output")] = "text",
    config: Annotated[Path, typer.Option("--config")] = Path("storehelper.yaml"),
) -> None:
    """Upload and publish an Android APK/AAB to Huawei AppGallery."""

    output_format = _output(output)
    if store != "huawei":
        _abort(
            PublishingError(
                "STORE_UNSUPPORTED",
                "The first StoreHelper release supports only --store huawei.",
                ExitCode.USAGE,
            ),
            output_format,
        )
    if release_notes is not None and release_notes_file is not None:
        _abort(
            PublishingError(
                "RELEASE_NOTES_CONFLICT",
                "Use either --release-notes or --release-notes-file, not both.",
                ExitCode.USAGE,
            ),
            output_format,
        )
    if release_notes_file is not None:
        try:
            release_notes = release_notes_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            _abort(
                PublishingError(
                    "RELEASE_NOTES_READ_FAILED",
                    "Release notes file is not readable UTF-8 text.",
                    ExitCode.USAGE,
                ),
                output_format,
            )
    submit = not no_submit and not dry_run
    interactive = _interactive(output_format)
    if submit and not yes:
        if not interactive:
            _abort(
                PublishingError(
                    "PUBLISH_CONFIRMATION_REQUIRED",
                    "Non-interactive publishing requires --yes.",
                    ExitCode.USAGE,
                ),
                output_format,
            )
        if not typer.confirm("Upload, update release notes, and submit to Huawei review?"):
            raise typer.Exit(code=int(ExitCode.USAGE))
        yes = True
    if submit and release_notes is None:
        if interactive:
            release_notes = typer.prompt("Huawei release notes (1-500 characters)")
        else:
            _abort(
                PublishingError(
                    "RELEASE_NOTES_MISSING",
                    "Publishing requires --release-notes or --release-notes-file.",
                    ExitCode.USAGE,
                ),
                output_format,
            )
    interval = _duration(poll_interval)
    timeout = _duration(wait_timeout)
    try:
        request = PublishRequest(
            app_alias=app_alias or "",
            file=file,
            release_notes=release_notes,
            submit=submit,
            dry_run=dry_run,
            confirmed=yes,
            poll_interval_seconds=interval,
            wait_timeout_seconds=timeout,
        )
    except ValueError:
        _abort(
            PublishingError(
                "PUBLISH_OPTIONS_INVALID",
                "Poll interval must be at least 5s and timeout must be at least 5s.",
                ExitCode.USAGE,
            ),
            output_format,
        )
    _run_operation(
        _publish_operation(
            request=request,
            config_path=config,
            app_alias=app_alias,
            interactive=interactive,
        ),
        output_format,
    )


@app.command()
def resume(
    run_id: Annotated[str, typer.Argument()],
    app_alias: Annotated[str | None, typer.Option("--app")] = None,
    poll_interval: Annotated[str, typer.Option("--poll-interval")] = "15s",
    wait_timeout: Annotated[str, typer.Option("--wait-timeout")] = "10m",
    output: Annotated[str, typer.Option("--output")] = "text",
    config: Annotated[Path, typer.Option("--config")] = Path("storehelper.yaml"),
) -> None:
    """Continue a saved run from its earliest safe operation."""

    output_format = _output(output)
    _run_operation(
        _resume_operation(
            run_id=run_id,
            config_path=config,
            app_alias=app_alias,
            interactive=_interactive(output_format),
            poll_interval=_duration(poll_interval),
            wait_timeout=_duration(wait_timeout),
        ),
        output_format,
    )


@app.command()
def status(
    app_alias: Annotated[str | None, typer.Option("--app")] = None,
    output: Annotated[str, typer.Option("--output")] = "text",
    config: Annotated[Path, typer.Option("--config")] = Path("storehelper.yaml"),
) -> None:
    """Query Huawei's current review state for an application."""

    output_format = _output(output)
    _run_operation(
        _status_operation(
            config_path=config,
            app_alias=app_alias,
            interactive=_interactive(output_format),
        ),
        output_format,
    )


@runs_app.command("list")
def runs_list(output: Annotated[str, typer.Option("--output")] = "text") -> None:
    """List local redacted publishing runs."""

    output_format = _output(output)
    try:
        receipts = RunRepository(RUNS_ROOT).list()
    except StoreHelperError as error:
        _abort(error, output_format)
    if output_format == "json":
        typer.echo(
            json.dumps(
                {"runs": [receipt.model_dump(mode="json") for receipt in receipts]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    elif not receipts:
        typer.echo("No publishing runs.")
    else:
        for receipt in receipts:
            typer.echo(f"{receipt.run_id}  {receipt.app_alias}  {receipt.state.value}")


@runs_app.command("show")
def runs_show(
    run_id: Annotated[str, typer.Argument()],
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    """Show one local redacted publishing run."""

    output_format = _output(output)
    try:
        receipt = RunRepository(RUNS_ROOT).get(run_id)
    except StoreHelperError as error:
        _abort(error, output_format)
    if output_format == "json":
        typer.echo(receipt.model_dump_json())
    else:
        typer.echo(f"Run: {receipt.run_id}")
        typer.echo(f"App: {receipt.app_alias} ({receipt.app_id})")
        typer.echo(f"State: {receipt.state.value}")
        typer.echo(f"Package: {receipt.logical_name}")


@runs_app.command("delete")
def runs_delete(
    run_id: Annotated[str, typer.Argument()],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    """Delete one local receipt; this never changes Huawei state."""

    output_format = _output(output)
    if not yes and (output_format == "json" or not typer.confirm(f"Delete local run {run_id}?")):
        raise typer.Exit(code=int(ExitCode.USAGE))
    try:
        deleted = RunRepository(RUNS_ROOT).delete(run_id)
    except StoreHelperError as error:
        _abort(error, output_format)
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "deleted": deleted}, separators=(",", ":")))
    else:
        typer.echo("Deleted." if deleted else "Run was already absent.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
