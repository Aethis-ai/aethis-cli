"""Where the free line is, and where the invite-only line is.

Evaluating published rulesets needs no account and no key; authoring is
invite-only. A developer must be able to learn that from `--help`, from the
README, and -- most importantly -- from the error they get when they hit the
boundary, without having to go and read a web page first.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
REPO = Path(__file__).parent.parent


def strip(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _help(argv):
    from aethis_cli.main import app

    result = CliRunner().invoke(app, argv, catch_exceptions=False)
    # Rich/Click wraps help text; collapse whitespace so assertions are about
    # content rather than terminal width.
    return re.sub(r"\s+", " ", strip(result.output))


def test_root_help_states_both_sides_of_the_boundary():
    text = _help(["--help"])
    assert "no API key" in text
    assert "invite-only" in text
    assert "aethis.ai/developer-access" in text


@pytest.mark.parametrize("command", ["decide", "explain"])
def test_evaluation_commands_say_no_key_required(command):
    text = _help([command, "--help"])
    assert "No API key required" in text
    assert "invite-only" in text


def test_decide_help_documents_the_exit_codes():
    text = _help(["decide", "--help"])
    assert "Exit codes" in text
    assert "3" in text and "rejected" in text


def test_auth_required_error_names_the_invite_boundary(monkeypatch, tmp_path):
    """The moment a developer actually hits the wall is the moment the message
    has to be useful."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)

    from aethis_cli.main import app

    # `AuthRequired` is translated into an exit status by the console-script
    # wrapper, which CliRunner does not go through -- the user-facing line is
    # printed either way, and that line is what this test is about.
    result = CliRunner().invoke(app, ["--no-prompt", "projects", "list"])
    text = re.sub(r"\s+", " ", strip(result.output))
    assert result.exit_code != 0
    assert "invite-only" in text
    assert "developer-access" in text
    assert "needs no key" in text


def test_readme_states_the_boundary():
    readme = (REPO / "README.md").read_text()
    assert "invite-only" in readme or "private beta" in readme
    assert "no key required" in readme.lower()
    assert "aethis.ai/developer-access" in readme
