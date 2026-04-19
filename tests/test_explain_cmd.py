"""Tests for `aethis explain` — ID validation, no-config fallback, error UX."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner


def test_explain_rejects_project_id_without_api_call(tmp_path, monkeypatch):
    """Passing a proj_* id exits 1 with a semantic hint, never hitting the API."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")

    client = MagicMock()
    with patch(
        "aethis_cli.commands.explain_cmd.load_client_or_fallback",
        return_value=(MagicMock(config_path=tmp_path), client),
    ):
        from aethis_cli.main import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["explain", "-b", "proj_i1HyinBtFJniayUC"],
            catch_exceptions=False,
        )

    assert result.exit_code == 1
    assert "Project ID" in result.output
    client.explain.assert_not_called()


def test_explain_works_without_aethis_yaml(tmp_path, monkeypatch):
    """From a directory with no aethis.yaml, explain succeeds via DEFAULT_BASE_URL fallback."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)

    client = MagicMock()
    client.explain.return_value = {
        "criteria": [{"group": "g1", "title": "Adult", "rule_text": "age >= 18"}],
    }

    with patch("aethis_cli.client.AethisClient", return_value=client):
        from aethis_cli.main import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["explain", "-b", "example:20260408-abc1234"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert "age >= 18" in result.output
    client.explain.assert_called_once_with("example:20260408-abc1234")


def test_explain_missing_bundle_id_gives_one_line_error(tmp_path, monkeypatch):
    """No --bundle-id and no state.json → one-line error, not a traceback."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")

    client = MagicMock()
    with patch(
        "aethis_cli.commands.explain_cmd.load_client_or_fallback",
        return_value=(MagicMock(config_path=tmp_path), client),
    ):
        from aethis_cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["explain"], catch_exceptions=False)

    assert result.exit_code == 1
    assert "No bundle_id" in result.output
    assert "Traceback" not in result.output
    client.explain.assert_not_called()


def test_explain_config_error_renders_without_rich_traceback(tmp_path, monkeypatch):
    """A ConfigError inside the command surfaces as a single line via cli(),
    not a Rich traceback panel. This is the regression that motivated
    pretty_exceptions_enable=False."""
    monkeypatch.chdir(tmp_path)

    from aethis_cli.errors import ConfigError

    with patch(
        "aethis_cli.commands.explain_cmd.load_client_or_fallback",
        side_effect=ConfigError("API key not found. Run 'aethis login'."),
    ):
        from aethis_cli.main import app

        runner = CliRunner()
        # catch_exceptions=True so the CliRunner receives the re-raised ConfigError
        # and we can inspect the exit flow. The top-level cli() wrapper catches
        # ConfigError; but app() (what CliRunner invokes) does not — so here we
        # just verify no Rich panel was emitted.
        result = runner.invoke(
            app,
            ["explain", "-b", "example:20260408-abc1234"],
            catch_exceptions=True,
        )

    # Whether it exits via ConfigError propagation or caught, the key
    # assertion is: no Rich traceback panel in the captured output.
    assert "╭─" not in result.output
