"""Tests for `aethis fields` — slug acceptance, no-config fallback, subcommands."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from aethis_cli.commands import generate_cmd


def _cfg(tmp_path, **over):
    """A minimal ProjectConfig-shaped object for the fields subcommands."""
    defaults = dict(config_path=tmp_path, project_id="proj_1", base_url="https://api.aethis.ai", project="p")
    defaults.update(over)
    return SimpleNamespace(**defaults)


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
        "aethis_cli.commands.fields_cmd.load_client_or_anon",
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


def test_fields_works_without_aethis_yaml_using_ruleset_id(tmp_path, monkeypatch):
    """Ruleset IDs work the same as slugs from outside a project."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)

    client = MagicMock()
    client.get_schema.return_value = {"fields": []}

    with patch(
        "aethis_cli.commands.fields_cmd.load_client_or_anon",
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
    client.get_schema.assert_called_once_with("spacecraft-crew-certification:20260407-933531f7")


def test_fields_missing_ruleset_id_gives_one_line_error(tmp_path, monkeypatch):
    """No --ruleset-id and no state.json → one-line error, not a traceback."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")

    client = MagicMock()
    with patch(
        "aethis_cli.commands.fields_cmd.load_client_or_anon",
        return_value=(MagicMock(config_path=tmp_path), client),
    ):
        from aethis_cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["fields"], catch_exceptions=False)

    assert result.exit_code == 1
    assert "No ruleset_id" in result.output
    assert "Traceback" not in result.output
    client.get_schema.assert_not_called()


# --- fields discover -------------------------------------------------------


def test_fields_discover_seeds_new_fields_and_preserves_existing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fields").mkdir()
    (tmp_path / "fields" / "fields.yaml").write_text(
        "fields:\n  - key: applicant.income\n    type: int\n    label: Income\n"
    )

    client = MagicMock()
    client.discover_fields.return_value = {
        "fields": [
            {"key": "applicant.income", "field_type": "integer"},  # already present → kept
            {"key": "applicant.dob", "field_type": "date", "question": "DOB?"},
            {"key": "applicant.kind", "field_type": "enum", "enum_values": ["a", "b"]},
        ],
        "completeness_score": 0.8,
        "critical_gaps": ["needs a residence field"],
        "recommendation": "add residence",
    }

    with (
        patch("aethis_cli.commands.fields_cmd.load_project_config", return_value=_cfg(tmp_path)),
        patch("aethis_cli.commands.fields_cmd.resolve_api_key", return_value="ak"),
        patch("aethis_cli.commands.fields_cmd.resolve_anthropic_key", return_value="sk"),
        patch("aethis_cli.commands.fields_cmd.make_authed_client", return_value=client),
    ):
        from aethis_cli.main import app

        result = CliRunner().invoke(app, ["fields", "discover"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    parsed = generate_cmd._parse_fields_yaml(tmp_path / "fields" / "fields.yaml")
    assert set(parsed) == {"applicant.income", "applicant.dob", "applicant.kind"}
    # The pre-existing hand-authored label is not clobbered.
    assert parsed["applicant.income"]["label"] == "Income"
    assert parsed["applicant.kind"]["enum_values"] == ["a", "b"]
    assert "Completeness" in result.output
    assert "needs a residence field" in result.output


# --- fields pull -----------------------------------------------------------


def test_fields_pull_writes_server_fields_and_keeps_local_annotations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fields").mkdir()
    (tmp_path / "fields" / "fields.yaml").write_text(
        "fields:\n"
        "  - key: applicant.income\n    type: int\n    hints:\n      - keep me\n"
        "  - key: applicant.local_only\n    type: string\n"
    )

    client = MagicMock()
    client.get_schema.return_value = {
        "fields": [
            {"field_id": "applicant.income", "field_type": "integer", "question": "Income?"},
            {"field_id": "applicant.new", "field_type": "boolean"},
        ]
    }

    with (
        patch("aethis_cli.commands.fields_cmd.load_project_config", return_value=_cfg(tmp_path)),
        patch("aethis_cli.commands.fields_cmd.resolve_api_key", return_value="ak"),
        patch("aethis_cli.commands.fields_cmd.make_authed_client", return_value=client),
    ):
        from aethis_cli.main import app

        result = CliRunner().invoke(app, ["fields", "pull", "-b", "rs_1"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    client.get_schema.assert_called_once_with("rs_1")
    parsed = generate_cmd._parse_fields_yaml(tmp_path / "fields" / "fields.yaml")
    assert parsed["applicant.income"]["hints"] == ["keep me"]  # local annotation preserved
    assert parsed["applicant.new"]["type"] == "bool"  # server form folded
    assert "applicant.local_only" in parsed  # local-only kept
    assert "local-only" in result.output


# --- fields validate -------------------------------------------------------


def test_fields_validate_passes_and_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fields").mkdir()
    fields_yaml = tmp_path / "fields" / "fields.yaml"

    from aethis_cli.main import app

    with patch("aethis_cli.commands.fields_cmd.load_project_config", return_value=_cfg(tmp_path)):
        fields_yaml.write_text("fields:\n  - key: a.income\n    type: int\n")
        ok = CliRunner().invoke(app, ["fields", "validate"], catch_exceptions=False)
        assert ok.exit_code == 0, ok.output
        assert "valid" in ok.output

        fields_yaml.write_text("fields:\n  - key: a.kind\n    type: enum\n")  # enum without values
        bad = CliRunner().invoke(app, ["fields", "validate"], catch_exceptions=False)
        assert bad.exit_code == 1
        assert "enum_values" in bad.output
