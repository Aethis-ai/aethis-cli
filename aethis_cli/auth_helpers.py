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

from aethis_cli.errors import AuthRequired


@dataclass
class RuntimeOptions:
    """CLI-wide flags wired in from the root callback in ``main.py``.

    Mutable singleton so commands and the HTTP client can both read the
    user's choices without threading flags through every signature.
    """

    no_prompt: bool = False
    api_key_override: Optional[str] = None
    base_url_override: Optional[str] = None
    profile_override: Optional[str] = None


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


def resolve_cached_key() -> Optional[str]:
    """The one place an Aethis API key is resolved; every command goes through it.

    Resolution order:

    0. The ``anonymous`` profile means no key at all: nothing below is consulted.
    1. ``--api-key`` (set on ``RUNTIME``) — the one-shot override.
    2. The environment variable the user designated: the name in
       ``AETHIS_API_KEY_ENV``, else ``AETHIS_API_KEY``. When a non-default name
       is designated and that variable is empty, ``AETHIS_API_KEY`` is NOT read
       as a fallback: one session never acts as two identities.
    3. The active profile's ``api_key`` field in ``~/.config/aethis/credentials``.
    4. For the ``default`` profile only: the OS keychain (legacy single-key
       storage location).
    5. Legacy ``.yaml``-suffixed credentials file (older builds).
    """
    from aethis_cli.config import (
        ANONYMOUS_PROFILE,
        DEFAULT_PROFILE,
        active_profile_name,
        check_project_api_key_env,
        designated_api_key_env,
        get_profile,
    )

    profile_name = active_profile_name()
    if profile_name == ANONYMOUS_PROFILE:
        return None

    if RUNTIME.api_key_override:
        return RUNTIME.api_key_override

    # A project file may not name which variable is read as the key: refuse (without
    # reading it) here, in the one place every command resolves a key.
    check_project_api_key_env()
    key = os.environ.get(designated_api_key_env())
    if key:
        return key

    profile = get_profile(profile_name)
    if profile.get("api_key"):
        return profile["api_key"]

    # Keychain only stores the default profile's key. Other profiles are
    # file-only — keep the keychain interface single-tenant to stay
    # backwards-compatible with pre-profile installs.
    if profile_name == DEFAULT_PROFILE:
        try:
            import keyring  # type: ignore[import-not-found]

            cached = keyring.get_password("aethis-cli", "api_key")
            if cached:
                return cached
        except Exception:
            pass

        from pathlib import Path

        import yaml  # type: ignore[import-untyped]

        from aethis_cli.config import credentials_path

        legacy = Path(str(credentials_path()) + ".yaml")
        if legacy.exists():
            try:
                raw = yaml.safe_load(legacy.read_text()) or {}
                return raw.get("api_key")
            except Exception:
                pass
    return None


def is_anonymous_active() -> bool:
    """Return True when the active profile is the reserved ``anonymous`` slot.

    Callers that build an authenticated client should use this to short-circuit
    to ``make_anonymous_client`` instead, so an admin who runs
    ``aethis --profile anonymous rulesets list`` doesn't accidentally fall
    through to the inline browser sign-in flow.
    """
    from aethis_cli.config import ANONYMOUS_PROFILE, active_profile_name

    return active_profile_name() == ANONYMOUS_PROFILE


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

    The server is always the one the user selected (``--base-url`` /
    ``AETHIS_BASE_URL``, else the active profile's, else the default). A
    ``base_url`` argument that names a different server (e.g. from a project
    ``aethis.yaml``) is refused before anything is prompted or sent: the
    browser flow sends a sign-in token to that server and saves the key it
    returns.
    """
    if is_anonymous_active():
        # ``--profile anonymous`` is an explicit "use no key" — surface that
        # decision instead of silently falling into the browser flow.
        message = (
            "Active profile is 'anonymous' — this command is an authoring "
            "command and needs an API key. Switch profiles with `aethis "
            "profile use <name>` or pass --profile <name>. Authoring is "
            "invite-only — request access at https://aethis.ai/developer-access."
        )
        from aethis_cli.output import console

        console.print(f"[red]Auth required:[/red] {message}")
        raise AuthRequired(message)

    if RUNTIME.api_key_override:
        return RUNTIME.api_key_override

    if not force_browser:
        cached = resolve_cached_key()
        if cached:
            return cached

    if RUNTIME.no_prompt or not _is_interactive():
        # Print the message ourselves rather than relying on the CLI wrapper —
        # the wrapper isn't reachable from CliRunner-style integration tests
        # (which call ``app()`` directly), and emitting the same line in both
        # paths keeps stderr consistent for users who pipe / log the CLI.
        from aethis_cli.output import console

        message = (
            "No API key found. Run 'aethis login' to authenticate, set "
            "AETHIS_API_KEY, or pass --api-key. Authoring is invite-only — "
            "request access at https://aethis.ai/developer-access. Evaluating "
            "published rulesets (decide / fields / explain) needs no key."
        )
        console.print(f"[red]Auth required:[/red] {message}")
        raise AuthRequired(message)

    # The sign-in token and the minted key only ever go to / are saved for the
    # server the user selected — never one a project file chose.
    from aethis_cli.config import authorize_credential_server

    resolved_base_url = authorize_credential_server(base_url)

    # Refuse a wrong save target now, before asking anything: the prompt must not
    # lead to a sign-in that is only refused afterwards.
    from aethis_cli.config import active_profile_name, check_save_target, resolve_credential_base_url

    check_save_target(resolved_base_url, resolve_credential_base_url()[1], active_profile_name(), "login")

    from aethis_cli.output import console

    if force_browser:
        console.print("\n[yellow]API key was rejected (HTTP 401).[/yellow]")
        proceed = _prompt_yes_no("Open browser to sign in again?", default=True)
    else:
        console.print("\n[yellow]No API key found.[/yellow]")
        proceed = _prompt_yes_no("Open browser to sign in?", default=True)

    if not proceed:
        message = (
            "Authentication declined. Run 'aethis login' to authenticate, set "
            "AETHIS_API_KEY, or pass --api-key. Authoring is invite-only — "
            "request access at https://aethis.ai/developer-access."
        )
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
