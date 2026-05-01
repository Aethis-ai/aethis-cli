"""Lazy-auth glue between cached credentials and the browser sign-in flow.

The CLI used to fail hard whenever an authenticated command ran without a
cached API key — the user got "API key not found. Run 'aethis login'."
and had to start over. This module gives commands a single entry point that
behaves like ``gcloud auth`` / ``vercel`` / ``gh``: if no key is cached, it
offers an inline browser sign-in (TTY only), runs it, persists the new key,
and returns it. Non-interactive callers (CI, piped input, ``--no-prompt``)
get a clean ``AuthRequired`` instead of a hung prompt.

Used in two places:

* Up-front, before constructing an authenticated client (preferred — gives
  the user a chance to authenticate before any HTTP call).
* On a 401 from the server, via the client's refresh hook, so a stale key
  triggers exactly one re-auth attempt before surfacing the original error.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional

from aethis_cli.errors import AuthRequired, ConfigError


@dataclass
class RuntimeOptions:
    """CLI-wide flags wired in from the root callback in ``main.py``.

    Mutable singleton so commands and the HTTP client can both read the
    user's choices without threading flags through every signature.
    """

    no_prompt: bool = False
    api_key_override: Optional[str] = None
    base_url_override: Optional[str] = None


# Module-level singleton; ``main.cli`` populates it before dispatching.
RUNTIME = RuntimeOptions()


def _is_interactive() -> bool:
    """Return True iff stdin and stdout are both attached to a TTY.

    Both ends matter: we need stdin to read the y/N answer, and stdout so the
    prompt is visible. Piped or CI invocations should never block on a prompt.
    """
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _resolve_cached_key() -> Optional[str]:
    """Return the cached key without raising. Mirrors ``resolve_api_key``'s
    fallback chain (env > keychain > credentials file) but never errors out.
    """
    key = os.environ.get("AETHIS_API_KEY")
    if key:
        return key

    try:
        import keyring  # type: ignore[import-not-found]

        key = keyring.get_password("aethis-cli", "api_key")
        if key:
            return key
    except Exception:
        pass

    from pathlib import Path

    import yaml  # type: ignore[import-untyped]

    from aethis_cli.config import credentials_path

    creds = credentials_path()
    if creds.exists():
        try:
            raw = yaml.safe_load(creds.read_text()) or {}
            cached = raw.get("api_key")
            if cached:
                return cached
        except Exception:
            pass
    # Older builds wrote a `.yaml` extension; honour that too.
    legacy = Path(str(creds) + ".yaml")
    if legacy.exists():
        try:
            raw = yaml.safe_load(legacy.read_text()) or {}
            return raw.get("api_key")
        except Exception:
            pass
    return None


def _prompt_yes_no(message: str, default: bool = True) -> bool:
    """Minimal y/N prompt that defaults to ``default`` on bare return.

    Kept dependency-free (no ``typer.confirm``) so the helper can be invoked
    from inside the HTTP client without a Typer context.
    """
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(message + suffix).strip().lower()
    except EOFError:
        return False
    if not answer:
        return default
    return answer in {"y", "yes"}


def require_auth_or_login_inline(
    base_url: Optional[str] = None,
    *,
    force_browser: bool = False,
    timeout: int = 120,
) -> str:
    """Return a usable API key, prompting for browser sign-in if needed.

    Resolution order:

    1. ``--api-key`` global flag (set on ``RUNTIME``) — bypass everything.
    2. Cached key (env / keychain / credentials file). Skipped when
       ``force_browser=True`` so the 401-retry path can mint a fresh key.
    3. Interactive browser flow, if stdin/stdout are TTYs and ``--no-prompt``
       wasn't passed. Asks for confirmation first so an accidental authed
       command doesn't silently spawn a browser.
    4. Otherwise raise :class:`AuthRequired` with a one-line remediation.

    ``base_url`` falls back to ``RUNTIME.base_url_override`` then
    ``AETHIS_BASE_URL`` then the default — this matters because the browser
    flow mints a key against a specific server, and minting against prod when
    the user is targeting a local dev server would silently 401 forever.
    """
    if RUNTIME.api_key_override:
        return RUNTIME.api_key_override

    if not force_browser:
        cached = _resolve_cached_key()
        if cached:
            return cached

    if RUNTIME.no_prompt or not _is_interactive():
        # Print the message ourselves rather than relying on the CLI wrapper —
        # the wrapper isn't reachable from CliRunner-style integration tests
        # (which call ``app()`` directly), and emitting the same line in both
        # paths keeps stderr consistent for users who pipe / log the CLI.
        from aethis_cli.output import console

        message = "No API key found. Run 'aethis login' to authenticate, set AETHIS_API_KEY, or pass --api-key."
        console.print(f"[red]Auth required:[/red] {message}")
        raise AuthRequired(message)

    # Resolve base URL late so we honour both env and project config.
    resolved_base_url = base_url or RUNTIME.base_url_override or os.environ.get("AETHIS_BASE_URL")
    if resolved_base_url is None:
        try:
            from aethis_cli.config import resolve_base_url_with_source

            resolved_base_url, _ = resolve_base_url_with_source()
        except ConfigError:
            from aethis_cli.config import DEFAULT_BASE_URL

            resolved_base_url = DEFAULT_BASE_URL

    from aethis_cli.output import console

    if force_browser:
        console.print("\n[yellow]API key was rejected (HTTP 401).[/yellow]")
        proceed = _prompt_yes_no("Open browser to sign in again?", default=True)
    else:
        console.print("\n[yellow]No API key found.[/yellow]")
        proceed = _prompt_yes_no("Open browser to sign in?", default=True)

    if not proceed:
        message = "Authentication declined. Run 'aethis login' to authenticate, set AETHIS_API_KEY, or pass --api-key."
        console.print(f"[red]Auth required:[/red] {message}")
        raise AuthRequired(message)

    # Lazy import — avoids a circular import at module load time.
    from aethis_cli.commands.login_cmd import run_browser_login

    new_key = run_browser_login(resolved_base_url, timeout=timeout)
    if not new_key:
        message = "Browser sign-in did not complete. Run 'aethis login' to retry."
        console.print(f"[red]Auth required:[/red] {message}")
        raise AuthRequired(message)
    return new_key


# NOTE: a higher-level ``make_authed_client`` lives in ``config.py`` so command
# modules that already import from ``config`` don't need a second import. This
# module deliberately stops at the credential-resolution boundary.
