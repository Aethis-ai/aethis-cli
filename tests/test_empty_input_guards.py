"""
Tests that `aethis generate` fails fast when the project has no source
documents, and that `aethis test` warns on zero test cases.

Regression guards for B7 in the public-release readiness review.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from typer.testing import CliRunner


def _make_project(tmp_path: Path, *, include_sources: bool, include_tests: bool) -> Path:
    """Minimal scaffolded Aethis project at tmp_path/myproj.

    Layout matches ``aethis init``:
        myproj/aethis.yaml
        myproj/sources/
        myproj/guidance/hints.yaml
        myproj/tests/scenarios.yaml
    """
    project = tmp_path / "myproj"
    (project / "sources").mkdir(parents=True)
    (project / "guidance").mkdir()
    (project / "tests").mkdir()

    (project / "aethis.yaml").write_text(
        "project: myproj\napi_key_env: AETHIS_API_KEY\nbase_url: http://localhost:8080\n"
    )
    (project / ".aethis").mkdir()
    (project / ".aethis" / "state.json").write_text('{"project_id": "proj_test"}')

    if include_sources:
        (project / "sources" / "policy.md").write_text("# Some policy text\n")

    if include_tests:
        (project / "tests" / "scenarios.yaml").write_text(
            yaml.dump({"tests": [{"name": "t1", "inputs": {}, "expect": {"outcome": "eligible"}}]})
        )
    else:
        (project / "tests" / "scenarios.yaml").write_text(yaml.dump({"tests": []}))
    return project


def test_generate_with_empty_sources_dir_fails_fast(tmp_path, monkeypatch):
    """No files in .aethis/sources/ → exit non-zero with a clear message before hitting the API."""
    project = _make_project(tmp_path, include_sources=False, include_tests=True)
    monkeypatch.chdir(project)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")

    mock_client = MagicMock()
    mock_client.upload_sources = MagicMock()
    mock_client.generate = MagicMock()

    with patch("aethis_cli.commands.generate_cmd.AethisClient", return_value=mock_client):
        from aethis_cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["generate"], catch_exceptions=False)

    assert result.exit_code != 0, result.output
    # Must not have triggered generation.
    mock_client.generate.assert_not_called()
    mock_client.upload_sources.assert_not_called()
    assert "source" in result.output.lower()


def test_test_with_zero_test_cases_warns_and_fails(tmp_path, monkeypatch):
    """Running `aethis test` with no scenarios → clear warning, non-zero exit."""
    project = _make_project(tmp_path, include_sources=True, include_tests=False)
    monkeypatch.chdir(project)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")

    mock_client = MagicMock()
    # If called, it returns the degenerate 0/0 shape.
    mock_client.run_tests.return_value = {
        "passed": 0,
        "total": 0,
        "failed": 0,
        "errors": 0,
        "results": [],
    }

    with patch("aethis_cli.commands.test_cmd.AethisClient", return_value=mock_client):
        from aethis_cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["test"], catch_exceptions=False)

    assert result.exit_code != 0, (
        f"zero-test-case run should exit non-zero; got exit={result.exit_code}\noutput: {result.output}"
    )
    assert "no test cases" in result.output.lower() or "zero" in result.output.lower()
