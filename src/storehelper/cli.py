"""StoreHelper command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from storehelper import __version__
from storehelper.commands.config import initialize, validate
from storehelper.commands.credentials import delete_profile, import_profile, list_profiles
from storehelper.credentials.providers import KeyringStore, SystemKeyring
from storehelper.domain.errors import StoreHelperError
from storehelper.output.renderers import OutputFormat, render_error

app = typer.Typer(
    name="storehelper",
    help="Local-first app store publishing for developers, CI/CD, and AI agents.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Validate project configuration.")
credentials_app = typer.Typer(help="Manage Huawei Service Account profiles securely.")
app.add_typer(config_app, name="config")
app.add_typer(credentials_app, name="credentials")

KEYRING: KeyringStore = SystemKeyring()


def _output(value: str) -> OutputFormat:
    if value not in ("text", "json"):
        raise typer.BadParameter("must be text or json", param_hint="--output")
    return value  # type: ignore[return-value]


def _abort(error: StoreHelperError, output: OutputFormat) -> None:
    render_error(error, output=output)
    raise typer.Exit(code=int(error.exit_code))


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


@app.command()
def publish() -> None:
    """Upload and publish an Android APK/AAB to Huawei AppGallery."""

    typer.echo("Publishing command is being initialized.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
