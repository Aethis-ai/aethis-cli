"""
Tests that `aethis login` degrades gracefully on headless systems where
`webbrowser.open()` cannot launch a browser.

Regression guard for B5 in the public-release readiness review.
"""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner


def _run(extra_env=None):
    from aethis_cli.main import app

    runner = CliRunner()
    env = {"AETHIS_BASE_URL": "http://test.invalid"}
    if extra_env:
        env.update(extra_env)
    return runner.invoke(app, ["login"], env=env, catch_exceptions=False)


def test_login_handles_oserror_from_webbrowser_without_traceback():
    """When auth.authenticate_with_clerk raises OSError (headless system),
    login must fall through to the manual-key prompt instead of bubbling
    an unhandled OSError / traceback to the user.
    """
    from aethis_cli.errors import AuthenticationError  # noqa: F401

    # Simulate a headless system: authenticate_with_clerk raises OSError
    # (the same shape auth.py:156 raises when webbrowser.open returns False).
    with (
        patch(
            "aethis_cli.auth.authenticate_with_clerk",
            side_effect=OSError("webbrowser.open returned False"),
        ),
        patch(
            "aethis_cli.commands.login_cmd._prompt_manual_key",
            return_value=None,
        ) as prompt_mock,
    ):
        result = _run()

    # Manual-key fallback must have been invoked.
    assert prompt_mock.called, (
        "login should fall through to _prompt_manual_key on OSError, not raise the OSError to the user"
    )
    # Exit cleanly; no raw Python traceback surfaced.
    assert result.exit_code == 0
    assert "Traceback" not in result.output
