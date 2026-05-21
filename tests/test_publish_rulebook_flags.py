"""Tests for the `aethis publish --rulebook --ruleset-name` flags
added by Phase A.9 + the small CLI follow-up.

Pattern follows `tests/test_decide_cmd.py`: CliRunner + patch the
client class.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _runner_invoke(args, env=None):
    from aethis_cli.main import app

    runner = CliRunner()
    return runner.invoke(app, args, catch_exceptions=False, env=env or {})


def _project_dir(tmp_path):
    """Create the .aethis project config the CLI expects."""
    aethis_yaml = tmp_path / "aethis.yaml"
    aethis_yaml.write_text("project: test-project\napi_key_env: AETHIS_API_KEY\n")
    state = tmp_path / ".aethis"
    state.mkdir()
    (state / "state.json").write_text('{"project_id": "proj_test"}')


def test_publish_passes_rulebook_flags_to_client(tmp_path, monkeypatch):
    """`aethis publish --rulebook X --ruleset-name Y` threads both
    fields through to `client.publish(...)` as kwargs."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    _project_dir(tmp_path)

    client = MagicMock()
    client.run_tests.return_value = {"passed": 1, "failed": 0, "errors": 0, "total": 1}
    client.publish.return_value = {
        "ruleset_id": "rs_x",
        "rulebook_id": "rb_abc",
        "ruleset_name": "child_eligibility",
        "state": "testing",
    }

    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(
            [
                "publish",
                "--rulebook",
                "aethis/uk-fsm",
                "--ruleset-name",
                "child_eligibility",
            ]
        )

    assert result.exit_code == 0, result.output
    _args, kwargs = client.publish.call_args
    assert kwargs["rulebook_id"] == "aethis/uk-fsm"
    assert kwargs["ruleset_name"] == "child_eligibility"
    out = _strip(result.output)
    assert "rb_abc" in out  # rulebook surfaced
    assert "testing" in out  # state surfaced
    assert "promote-to-live" in out  # next-step hint shown


def test_publish_without_rulebook_flags_omits_them(tmp_path, monkeypatch):
    """Legacy publish path: no rulebook flags, no rulebook fields in
    the client call."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    _project_dir(tmp_path)

    client = MagicMock()
    client.run_tests.return_value = {"passed": 1, "failed": 0, "errors": 0, "total": 1}
    client.publish.return_value = {"ruleset_id": "rs_x"}

    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["publish"])

    assert result.exit_code == 0
    _args, kwargs = client.publish.call_args
    assert kwargs.get("rulebook_id") is None
    assert kwargs.get("ruleset_name") is None


def test_publish_rulebook_without_ruleset_name_rejected(tmp_path, monkeypatch):
    """Partial input must fail loud at the CLI layer with a clear
    message, before the request is built."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    _project_dir(tmp_path)

    client = MagicMock()
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["publish", "--rulebook", "aethis/uk-fsm"])
    assert result.exit_code != 0
    out = _strip(result.output)
    assert "must be set together" in out
    client.publish.assert_not_called()


def test_publish_ruleset_name_without_rulebook_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    _project_dir(tmp_path)

    client = MagicMock()
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["publish", "--ruleset-name", "x"])
    assert result.exit_code != 0
    out = _strip(result.output)
    assert "must be set together" in out
    client.publish.assert_not_called()
