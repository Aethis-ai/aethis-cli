"""
Tests for the `--force` flag on `aethis publish`.

Regression guard for B6 in the public-release readiness review.

Contract:
- `publish` runs tests first and refuses to publish if any fail/error.
- `publish --force` skips the test gate and publishes anyway with a warning.
- Network/API errors on the test call still block publish unless --force.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner


def _invoke(args, *, patches):
    from aethis_cli.main import app

    runner = CliRunner()
    with patches:
        return runner.invoke(app, args, catch_exceptions=False)


def _mock_cfg_and_key():
    cfg = MagicMock()
    cfg.base_url = "http://test.invalid"
    cfg.project_id = "proj_test"
    cfg.config_path = "/tmp/.aethis"
    return cfg


@pytest.fixture
def base_patches():
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("aethis_cli.commands.publish_cmd.load_project_config", return_value=_mock_cfg_and_key()))
    stack.enter_context(patch("aethis_cli.commands.publish_cmd.resolve_api_key", return_value="ak_test"))
    yield stack
    stack.close()


def test_publish_without_force_refuses_when_tests_failing(base_patches):
    """publish with tests failing → exit 1, publish() never called."""
    mock_client = MagicMock()
    mock_client.run_tests.return_value = {
        "passed": 2,
        "total": 5,
        "failed": 3,
        "errors": 0,
        "results": [],
    }
    mock_client.publish = MagicMock()

    base_patches.enter_context(patch("aethis_cli.commands.publish_cmd.AethisClient", return_value=mock_client))

    from aethis_cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["publish"], catch_exceptions=False)

    assert result.exit_code == 1
    mock_client.publish.assert_not_called()
    assert "fail" in result.output.lower() or "refus" in result.output.lower() or "--force" in result.output


def test_publish_with_force_bypasses_failing_tests(base_patches):
    """publish --force with failing tests → still publishes, warning shown."""
    mock_client = MagicMock()
    mock_client.run_tests.return_value = {
        "passed": 2,
        "total": 5,
        "failed": 3,
        "errors": 0,
        "results": [],
    }
    mock_client.publish.return_value = {"bundle_id": "test:abc", "version": "v2"}

    base_patches.enter_context(patch("aethis_cli.commands.publish_cmd.AethisClient", return_value=mock_client))

    from aethis_cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["publish", "--force"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    mock_client.publish.assert_called_once()
    assert "force" in result.output.lower() or "warn" in result.output.lower()


def test_publish_passing_tests_publishes_without_force(base_patches):
    """publish with all tests passing → publishes without --force."""
    mock_client = MagicMock()
    mock_client.run_tests.return_value = {
        "passed": 5,
        "total": 5,
        "failed": 0,
        "errors": 0,
        "results": [],
    }
    mock_client.publish.return_value = {"bundle_id": "test:abc", "version": "v1"}

    base_patches.enter_context(patch("aethis_cli.commands.publish_cmd.AethisClient", return_value=mock_client))

    from aethis_cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["publish"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    mock_client.publish.assert_called_once()
