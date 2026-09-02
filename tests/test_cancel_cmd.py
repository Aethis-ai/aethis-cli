"""Tests for explicit, non-automatic generation cancellation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from aethis_cli.main import app


def _client(response: dict | None = None) -> MagicMock:
    client = MagicMock()
    client.get_status.return_value = {
        "project_status": "generating",
        "job": {"job_id": "job_1", "status": "running"},
    }
    client.cancel_generation.return_value = response or {
        "job_id": "job_1",
        "status": "failed",
        "project_released": True,
        "detail": "Ownership released. An already-running worker may continue.",
    }
    return client


def test_cancel_with_yes_uses_explicit_project_and_reports_worker_limitation():
    client = _client()
    with patch("aethis_cli.commands.cancel_cmd.load_client_or_fallback", return_value=(MagicMock(), client)):
        result = CliRunner().invoke(app, ["cancel", "-p", "proj_abc", "--yes"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    client.cancel_generation.assert_called_once_with("proj_abc", "job_1")
    assert "job_1" in result.output
    assert "project proj_abc released" in result.output
    assert "worker may continue" in result.output


def test_cancel_declined_observes_target_but_never_calls_cancel():
    client = _client()
    with patch(
        "aethis_cli.commands.cancel_cmd.load_client_or_fallback",
        return_value=(MagicMock(), client),
    ):
        result = CliRunner().invoke(app, ["cancel", "-p", "proj_abc"], input="n\n")

    assert result.exit_code == 1
    client.get_status.assert_called_once_with("proj_abc")
    client.cancel_generation.assert_not_called()


def test_cancel_in_ci_never_blocks_on_stdin_and_announces_the_bypass():
    client = _client()
    with patch("aethis_cli.commands.cancel_cmd.load_client_or_fallback", return_value=(MagicMock(), client)):
        result = CliRunner().invoke(
            app,
            ["cancel", "-p", "proj_ci"],
            env={"CI": "1"},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert "Non-interactive (CI set)" in result.output
    client.cancel_generation.assert_called_once_with("proj_ci", "job_1")


def test_cancel_json_emits_the_server_contract_without_human_prose():
    response = {
        "job_id": "job_json",
        "status": "failed",
        "project_released": True,
        "detail": "An already-running worker may continue.",
    }
    client = _client(response)
    with patch("aethis_cli.commands.cancel_cmd.load_client_or_fallback", return_value=(MagicMock(), client)):
        result = CliRunner().invoke(
            app,
            ["--output", "json", "cancel", "-p", "proj_json", "--yes"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == response


def test_cancel_without_project_context_fails_before_auth_or_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("aethis_cli.commands.cancel_cmd.load_client_or_fallback") as load_client:
        result = CliRunner().invoke(app, ["cancel", "--yes"], catch_exceptions=False)

    assert result.exit_code == 1
    assert "No project ID" in result.output
    load_client.assert_not_called()


def test_cancel_refuses_when_expected_job_differs_from_observed_job():
    client = _client()
    with patch(
        "aethis_cli.commands.cancel_cmd.load_client_or_fallback",
        return_value=(MagicMock(), client),
    ):
        result = CliRunner().invoke(
            app,
            ["cancel", "-p", "proj_abc", "--job-id", "job_old", "--yes"],
            catch_exceptions=False,
        )

    assert result.exit_code == 1
    assert "expected job job_old" in result.output
    assert "currently reports job_1" in result.output
    client.cancel_generation.assert_not_called()
