from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import storehelper.cli as cli_module
from storehelper.credentials.providers import MemoryKeyring

runner = CliRunner()


def test_help_and_version() -> None:
    help_result = runner.invoke(cli_module.app, ["--help"])
    version_result = runner.invoke(cli_module.app, ["version"])

    assert help_result.exit_code == 0
    assert "publish" in help_result.stdout
    assert version_result.exit_code == 0
    assert "0.1.0" in version_result.stdout


def test_init_does_not_overwrite_and_config_validate(tmp_path: Path) -> None:
    config = tmp_path / "storehelper.yaml"

    initialized = runner.invoke(cli_module.app, ["init", "--config", str(config)])
    validated = runner.invoke(
        cli_module.app,
        ["config", "validate", "--config", str(config)],
    )
    repeated = runner.invoke(cli_module.app, ["init", "--config", str(config)])

    assert initialized.exit_code == 0
    assert validated.exit_code == 0
    assert "valid" in validated.stdout.lower()
    assert repeated.exit_code == 2
    assert config.read_text(encoding="utf-8").startswith("version: 1")


def test_init_and_config_validate_json_output(tmp_path: Path) -> None:
    config = tmp_path / "storehelper.yaml"

    initialized = runner.invoke(
        cli_module.app,
        ["init", "--config", str(config), "--output", "json"],
    )
    validated = runner.invoke(
        cli_module.app,
        ["config", "validate", "--config", str(config), "--output", "json"],
    )

    assert json.loads(initialized.stdout)["ok"] is True
    assert json.loads(validated.stdout) == {"ok": True, "valid": True}


def test_invalid_config_json_error_is_one_document(tmp_path: Path) -> None:
    config = tmp_path / "bad.yaml"
    config.write_text("password: secret", encoding="utf-8")

    result = runner.invoke(
        cli_module.app,
        ["config", "validate", "--config", str(config), "--output", "json"],
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["code"] == "CONFIG_CONTAINS_SECRET"
    assert "secret" not in result.stdout


def test_credential_import_list_and_delete(
    tmp_path: Path,
    rsa_private_key: str,
    monkeypatch,
) -> None:
    keyring = MemoryKeyring()
    monkeypatch.setattr(cli_module, "KEYRING", keyring)
    credential_file = tmp_path / "huawei.json"
    credential_file.write_text(
        json.dumps(
            {
                "key_id": "kid-1",
                "sub_account": "sub-1",
                "private_key": rsa_private_key,
            }
        ),
        encoding="utf-8",
    )

    imported = runner.invoke(
        cli_module.app,
        [
            "credentials",
            "import",
            "--profile",
            "work",
            "--file",
            str(credential_file),
        ],
    )
    listed = runner.invoke(cli_module.app, ["credentials", "list", "--output", "json"])
    imported_json = runner.invoke(
        cli_module.app,
        [
            "credentials",
            "import",
            "--profile",
            "second",
            "--file",
            str(credential_file),
            "--output",
            "json",
        ],
    )
    deleted_json = runner.invoke(
        cli_module.app,
        ["credentials", "delete", "--profile", "second", "--yes", "--output", "json"],
    )
    deleted = runner.invoke(
        cli_module.app,
        ["credentials", "delete", "--profile", "work", "--yes"],
    )

    assert imported.exit_code == 0
    assert json.loads(listed.stdout) == {"profiles": ["work"]}
    assert json.loads(imported_json.stdout)["profile"] == "second"
    assert json.loads(deleted_json.stdout)["profile"] == "second"
    assert rsa_private_key not in imported.stdout + listed.stdout + deleted.stdout
    assert deleted.exit_code == 0


def test_empty_credential_list_and_delete_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "KEYRING", MemoryKeyring())

    listed = runner.invoke(cli_module.app, ["credentials", "list"])
    declined = runner.invoke(
        cli_module.app,
        ["credentials", "delete", "--profile", "work"],
        input="n\n",
    )

    assert listed.stdout.strip() == "No credential profiles."
    assert declined.exit_code == 2
