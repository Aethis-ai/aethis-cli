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

    base_patches.enter_context(patch("aethis_cli.commands.publish_cmd.make_authed_client", return_value=mock_client))

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
    mock_client.publish.return_value = {"ruleset_id": "test:abc", "version": "v2"}

    base_patches.enter_context(patch("aethis_cli.commands.publish_cmd.make_authed_client", return_value=mock_client))

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
    mock_client.publish.return_value = {"ruleset_id": "test:abc", "version": "v1"}

    base_patches.enter_context(patch("aethis_cli.commands.publish_cmd.make_authed_client", return_value=mock_client))

    from aethis_cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["publish"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    mock_client.publish.assert_called_once()
    # No --force on the wire either: server-side gate runs.
    _, call_kwargs = mock_client.publish.call_args
    assert call_kwargs.get("force_unsafe", False) is False


def test_publish_force_threads_force_unsafe_to_server(base_patches):
    """publish --force must pass force_unsafe=True so the server-side gate (aethis-core 0.11+) is also bypassed.

    Without this, --force only bypassed the CLI's local gate while a direct
    curl would have been refused by the server — and worse, prior to 0.11
    the server didn't gate at all, so --force hid a real failure mode.
    """
    mock_client = MagicMock()
    mock_client.run_tests.return_value = {
        "passed": 2,
        "total": 5,
        "failed": 3,
        "errors": 0,
        "results": [],
    }
    mock_client.publish.return_value = {"ruleset_id": "test:abc", "version": "v2"}

    base_patches.enter_context(patch("aethis_cli.commands.publish_cmd.make_authed_client", return_value=mock_client))

    from aethis_cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["publish", "--force"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    mock_client.publish.assert_called_once()
    _, call_kwargs = mock_client.publish.call_args
    assert call_kwargs.get("force_unsafe") is True, (
        f"publish --force must pass force_unsafe=True to the client, got call_kwargs={call_kwargs}"
    )


def test_client_publish_includes_force_unsafe_in_body_when_set():
    """AethisClient.publish(force_unsafe=True) puts the field on the wire."""
    from aethis_cli.client import AethisClient

    captured: dict = {}

    class _StubClient(AethisClient):
        def _request(self, method, path, **kw):
            captured["method"] = method
            captured["path"] = path
            captured["kwargs"] = kw
            return {"ok": True}

    client = _StubClient(base_url="http://test.invalid", api_key="ak_test")
    client.publish("proj_x", slug="foo/bar", force_unsafe=True)

    assert captured["kwargs"]["json"]["force_unsafe"] is True
    assert captured["kwargs"]["json"]["slug"] == "foo/bar"


def test_client_publish_omits_force_unsafe_when_default():
    """force_unsafe defaults to False and is omitted from the body to keep the wire payload minimal and old-engine compatible."""
    from aethis_cli.client import AethisClient

    captured: dict = {}

    class _StubClient(AethisClient):
        def _request(self, method, path, **kw):
            captured["method"] = method
            captured["path"] = path
            captured["kwargs"] = kw
            return {"ok": True}

    client = _StubClient(base_url="http://test.invalid", api_key="ak_test")
    client.publish("proj_x")

    # No body => no kwargs at all
    assert "json" not in captured["kwargs"], captured
