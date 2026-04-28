"""Tests for `aethis fields` — slug acceptance, no-config fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner


def test_fields_works_without_aethis_yaml_using_slug(tmp_path, monkeypatch):
    """From a directory with no aethis.yaml, `aethis fields -b <slug>` succeeds via fallback."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)

    client = MagicMock()
    client.get_schema.return_value = {
        "fields": [
            {
                "field_id": "applicant.school_year",
                "field_type": "string",
                "description": "Year of schooling",
                "enum_values": ["reception", "year-1", "year-2"],
            }
        ],
    }

    with patch(
        "aethis_cli.commands.fields_cmd.load_client_or_fallback",
        return_value=(MagicMock(config_path=tmp_path), client),
    ):
        from aethis_cli.main import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["fields", "-b", "aethis/uk-fsm/universal-infant"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert "applicant.school_year" in result.output
    client.get_schema.assert_called_once_with("aethis/uk-fsm/universal-infant")


def test_fields_works_without_aethis_yaml_using_bundle_id(tmp_path, monkeypatch):
    """Bundle IDs work the same as slugs from outside a project."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)

    client = MagicMock()
    client.get_schema.return_value = {"fields": []}

    with patch(
        "aethis_cli.commands.fields_cmd.load_client_or_fallback",
        return_value=(MagicMock(config_path=tmp_path), client),
    ):
        from aethis_cli.main import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["fields", "-b", "spacecraft-crew-certification:20260407-933531f7"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    client.get_schema.assert_called_once_with(
        "spacecraft-crew-certification:20260407-933531f7"
    )


def test_fields_missing_bundle_id_gives_one_line_error(tmp_path, monkeypatch):
    """No --bundle-id and no state.json → one-line error, not a traceback."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")

    client = MagicMock()
    with patch(
        "aethis_cli.commands.fields_cmd.load_client_or_fallback",
        return_value=(MagicMock(config_path=tmp_path), client),
    ):
        from aethis_cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["fields"], catch_exceptions=False)

    assert result.exit_code == 1
    assert "No bundle_id" in result.output
    assert "Traceback" not in result.output
    client.get_schema.assert_not_called()
