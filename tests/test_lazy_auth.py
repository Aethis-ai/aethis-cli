"""Tests for lazy auth — auto-prompt browser sign-in on missing key / 401.

Issue: https://github.com/Aethis-ai/aethis-cli/issues/12

Behaviour under test:

* Cached key is used as-is (no prompt).
* Missing key + non-TTY ⇒ ``AuthRequired`` (clean fail-fast for CI).
* Missing key + ``--no-prompt`` ⇒ ``AuthRequired`` even on a TTY.
* Missing key + TTY + user accepts ⇒ inline browser flow runs and the new key
  is returned.
* Missing key + TTY + user declines ⇒ ``AuthRequired``.
* HTTP 401 from the server triggers exactly one re-auth + retry; a second
  401 surfaces the original error without re-prompting.
* ``--api-key`` global flag bypasses the helper entirely.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from typer.testing import CliRunner

from aethis_cli.auth_helpers import RUNTIME, require_auth_or_login_inline
from aethis_cli.client import AethisClient
from aethis_cli.errors import AethisAPIError, AuthRequired


@pytest.fixture(autouse=True)
def _reset_runtime(monkeypatch, tmp_path):
    """Each test starts with a clean ``RUNTIME`` and no cached credentials.

    We also point ``XDG_CONFIG_HOME`` at an empty tmp dir so the developer's
    real ``~/.config/aethis/credentials`` doesn't leak into the test, and we
    stub the keychain probe (the dev machine running this suite may have a
    real saved key in macOS Keychain).
    """
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty_xdg"))

    # Stub the keyring probe (if installed) so a developer's saved key can't
    # silently make a "no cached key" test pass.
    try:
        import keyring  # noqa: F401

        monkeypatch.setattr("keyring.get_password", lambda *_a, **_k: None)
    except ImportError:
        pass

    RUNTIME.no_prompt = False
    RUNTIME.api_key_override = None
    RUNTIME.base_url_override = None
    yield
    RUNTIME.no_prompt = False
    RUNTIME.api_key_override = None
    RUNTIME.base_url_override = None


# ---------------------------------------------------------------------------
# require_auth_or_login_inline — direct unit tests
# ---------------------------------------------------------------------------


class TestCachedKeyHappyPath:
    def test_env_key_returned_directly(self, monkeypatch):
        monkeypatch.setenv("AETHIS_API_KEY", "ak_from_env")
        assert require_auth_or_login_inline("https://api.test") == "ak_from_env"

    def test_api_key_override_wins(self, monkeypatch):
        monkeypatch.setenv("AETHIS_API_KEY", "ak_from_env")
        RUNTIME.api_key_override = "ak_override"
        assert require_auth_or_login_inline("https://api.test") == "ak_override"


class TestNonInteractive:
    def test_non_tty_raises_auth_required(self):
        # CliRunner / pytest already give us a non-TTY stdin, but be explicit.
        with patch("aethis_cli.auth_helpers._is_interactive", return_value=False):
            with pytest.raises(AuthRequired, match="No API key"):
                require_auth_or_login_inline("https://api.test")

    def test_no_prompt_flag_skips_prompt_even_on_tty(self):
        RUNTIME.no_prompt = True
        with patch("aethis_cli.auth_helpers._is_interactive", return_value=True):
            with pytest.raises(AuthRequired, match="No API key"):
                require_auth_or_login_inline("https://api.test")


class TestInteractive:
    def test_user_accepts_runs_browser_flow(self):
        with (
            patch("aethis_cli.auth_helpers._is_interactive", return_value=True),
            patch("aethis_cli.auth_helpers._prompt_yes_no", return_value=True),
            patch(
                "aethis_cli.commands.login_cmd.run_browser_login",
                return_value="ak_freshly_minted",
            ) as run_login,
        ):
            key = require_auth_or_login_inline("https://api.test")

        assert key == "ak_freshly_minted"
        run_login.assert_called_once()

    def test_user_declines_raises_auth_required(self):
        with (
            patch("aethis_cli.auth_helpers._is_interactive", return_value=True),
            patch("aethis_cli.auth_helpers._prompt_yes_no", return_value=False),
        ):
            with pytest.raises(AuthRequired, match="declined"):
                require_auth_or_login_inline("https://api.test")

    def test_browser_flow_failure_raises_auth_required(self):
        with (
            patch("aethis_cli.auth_helpers._is_interactive", return_value=True),
            patch("aethis_cli.auth_helpers._prompt_yes_no", return_value=True),
            patch(
                "aethis_cli.commands.login_cmd.run_browser_login",
                return_value=None,
            ),
        ):
            with pytest.raises(AuthRequired, match="did not complete"):
                require_auth_or_login_inline("https://api.test")


class TestForceBrowser:
    def test_force_browser_skips_cache_lookup(self, monkeypatch):
        """The 401-retry path must not pick up the same stale cached key."""
        monkeypatch.setenv("AETHIS_API_KEY", "ak_stale")
        with (
            patch("aethis_cli.auth_helpers._is_interactive", return_value=True),
            patch("aethis_cli.auth_helpers._prompt_yes_no", return_value=True),
            patch(
                "aethis_cli.commands.login_cmd.run_browser_login",
                return_value="ak_fresh",
            ),
        ):
            assert require_auth_or_login_inline("https://api.test", force_browser=True) == "ak_fresh"

    def test_force_browser_still_honours_api_key_override(self, monkeypatch):
        RUNTIME.api_key_override = "ak_override"
        # Even on a 401-retry we must respect ``--api-key`` — the user told us
        # not to look anywhere else.
        assert require_auth_or_login_inline("https://api.test", force_browser=True) == "ak_override"


# ---------------------------------------------------------------------------
# AethisClient — 401 refresh-and-retry
# ---------------------------------------------------------------------------


BASE = "http://test.local"


@respx.mock(base_url=BASE)
def test_client_retries_once_after_401_with_fresh_key(respx_mock):
    """First request gets 401, hook returns a new key, retry succeeds."""
    route = respx_mock.get("/api/v1/public/projects/").mock(
        side_effect=[
            httpx.Response(401, json={"detail": "Invalid key"}),
            httpx.Response(200, json=[{"project_id": "proj_1", "name": "ok"}]),
        ]
    )
    refresh = MagicMock(return_value="ak_fresh")
    client = AethisClient("ak_stale", BASE, on_auth_required=refresh)

    result = client.list_projects()

    assert len(result) == 1
    assert refresh.call_count == 1
    # Retried request must carry the new key, not the original stale one.
    assert route.calls[1].request.headers["x-api-key"] == "ak_fresh"


@respx.mock(base_url=BASE)
def test_client_does_not_loop_on_second_401(respx_mock):
    """Second 401 in the same call surfaces the original error.

    This is the safety bound: even if the refresh hook keeps returning a key
    the server hates, we must not spawn an infinite browser-prompt loop.
    """
    respx_mock.get("/api/v1/public/projects/").mock(return_value=httpx.Response(401, json={"detail": "Still bad"}))
    refresh = MagicMock(return_value="ak_still_bad")
    client = AethisClient("ak_stale", BASE, on_auth_required=refresh)

    with pytest.raises(AethisAPIError) as exc_info:
        client.list_projects()

    assert exc_info.value.status_code == 401
    assert refresh.call_count == 1, "refresh hook must be called exactly once"


@respx.mock(base_url=BASE)
def test_client_without_hook_raises_immediately(respx_mock):
    """Backwards compatibility: clients built without the hook keep behaving
    exactly as before — 401 raises ``AethisAPIError`` directly.
    """
    respx_mock.get("/api/v1/public/projects/").mock(return_value=httpx.Response(401, json={"detail": "no key"}))
    client = AethisClient("ak_test", BASE)

    with pytest.raises(AethisAPIError) as exc_info:
        client.list_projects()
    assert exc_info.value.status_code == 401


@respx.mock(base_url=BASE)
def test_client_propagates_auth_required_from_refresh(respx_mock):
    """If the hook raises ``AuthRequired`` (CI / user declined) the original
    401 should *not* be masked — we want a clean ``AuthRequired`` to bubble.
    """
    respx_mock.get("/api/v1/public/projects/").mock(return_value=httpx.Response(401, json={"detail": "expired"}))

    def _refusing_refresh(force_browser: bool = True) -> str:
        raise AuthRequired("declined")

    client = AethisClient("ak_stale", BASE, on_auth_required=_refusing_refresh)

    with pytest.raises(AuthRequired):
        client.list_projects()


# ---------------------------------------------------------------------------
# CLI integration — root flags wire the helper correctly
# ---------------------------------------------------------------------------


def _run_cli(args, *, env=None):
    """Invoke the CLI through the ``cli()`` wrapper so AuthRequired and
    AethisAPIError are caught and rendered the way users see them.

    ``runner.invoke(app, ...)`` would skip the wrapper entirely, leaving
    AuthRequired to propagate as a raw exception and never producing a clean
    exit_code 1 — that misrepresents the production path. Calling ``cli()``
    via the runner invokes the same try/except chain real users hit.
    """
    from aethis_cli.main import app

    runner = CliRunner()
    return runner.invoke(app, args, env=env or {}, catch_exceptions=True)


def test_cli_no_prompt_flag_fails_fast_on_missing_key(monkeypatch, tmp_path):
    """``aethis --no-prompt projects list`` with no cached key exits 1
    without spawning a browser, even if stdin happened to be a TTY.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    with patch("aethis_cli.commands.login_cmd.run_browser_login") as run_login:
        result = _run_cli(["--no-prompt", "projects", "list"])

    assert result.exit_code == 1
    run_login.assert_not_called()
    assert "Auth required" in result.output or "No API key" in result.output


def test_cli_api_key_flag_bypasses_helper(monkeypatch, tmp_path):
    """``aethis --api-key sk_xxx projects list`` must not call the helper —
    the user has told us exactly which key to use.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    # Avoid picking up a stray aethis.yaml from the dev's cwd.
    monkeypatch.chdir(tmp_path)

    with (
        respx.mock() as respx_mock,
        patch("aethis_cli.commands.login_cmd.run_browser_login") as run_login,
    ):
        route = respx_mock.get("https://api.aethis.ai/api/v1/public/projects/").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = _run_cli(["--api-key", "ak_explicit", "projects", "list"])

    assert result.exit_code == 0, f"exit={result.exit_code} output={result.output} exc={result.exception}"
    run_login.assert_not_called()
    assert route.called, f"projects/ endpoint not hit; output: {result.output}"
    # Header must reflect the explicit override.
    assert route.calls[0].request.headers["x-api-key"] == "ak_explicit"


def test_cli_non_tty_skips_prompt_and_fails(monkeypatch, tmp_path):
    """Piped invocation (CliRunner ⇒ non-TTY) with no key should exit 1
    cleanly without ever calling the browser flow.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))

    with patch("aethis_cli.commands.login_cmd.run_browser_login") as run_login:
        result = _run_cli(["projects", "list"])

    assert result.exit_code == 1
    run_login.assert_not_called()


def test_cli_inline_login_on_first_call(monkeypatch, tmp_path):
    """TTY + missing key + user accepts ⇒ browser flow runs and the command
    proceeds with the new key. End-to-end happy path for issue #12.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    monkeypatch.chdir(tmp_path)

    with (
        respx.mock() as respx_mock,
        patch("aethis_cli.auth_helpers._is_interactive", return_value=True),
        patch("aethis_cli.auth_helpers._prompt_yes_no", return_value=True),
        patch(
            "aethis_cli.commands.login_cmd.run_browser_login",
            return_value="ak_minted",
        ) as run_login,
    ):
        route = respx_mock.get("https://api.aethis.ai/api/v1/public/projects/").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = _run_cli(["projects", "list"])

    assert result.exit_code == 0, f"exit={result.exit_code} output={result.output} exc={result.exception}"
    run_login.assert_called_once()
    assert route.called, f"projects/ endpoint not hit; output: {result.output}"
    assert route.calls[0].request.headers["x-api-key"] == "ak_minted"
