"""aethis login — authenticate via browser or store an existing API key."""

from __future__ import annotations

import os
from typing import Optional

import typer

from aethis_cli.config import (
    ANONYMOUS_PROFILE,
    DEFAULT_BASE_URL,
    DEFAULT_PROFILE,
    active_profile_name,
    credentials_path,
    set_profile,
)
from aethis_cli.errors import ConfigError
from aethis_cli.output import console, info, success

_KEYRING_SERVICE = "aethis-cli"
_KEYRING_USERNAME = "api_key"


def _save_to_keyring(api_key: str) -> bool:
    """Attempt to store key in OS keychain. Returns True on success."""
    try:
        import keyring

        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, api_key)
        return True
    except Exception:
        return False


def save_api_key(api_key: str, *, profile: Optional[str] = None, announce: bool = True) -> None:
    """Save API key to the best available credential store for ``profile``.

    Public helper so the lazy-auth flow can persist a freshly minted key
    without going through the typer command. ``announce`` controls whether
    the success line is printed (the inline-login path prints its own).

    Resolution:
    * ``profile=None`` → use the currently active profile.
    * Default profile → try OS keychain first (legacy single-key location);
      fall back to the credentials file's ``profiles.default`` slot.
    * Any other profile → write directly to the credentials file.
    * Reserved ``anonymous`` profile → refuse.
    """
    target = profile or active_profile_name()
    if target == ANONYMOUS_PROFILE:
        raise ConfigError(
            f"Cannot save a key to the reserved '{ANONYMOUS_PROFILE}' profile. "
            "Pick a different profile name with --profile."
        )

    if target == DEFAULT_PROFILE and _save_to_keyring(api_key):
        if announce:
            success("API key saved to system keychain (profile: default).")
        return

    set_profile(target, api_key=api_key)
    if announce:
        if target == DEFAULT_PROFILE:
            info("keyring not available — key saved to file (install 'keyring' for OS keychain)")
        success(f"API key saved to {credentials_path()} (profile: {target}).")


# Backwards-compatible alias used internally by this module.
_save_key = save_api_key


def run_browser_login(base_url: str, timeout: int = 120, *, profile: Optional[str] = None) -> Optional[str]:
    """Run the full browser OAuth + key-mint flow and return the new API key.

    On success, persists the key to the keychain/credentials file and returns
    it. Returns ``None`` if any step fails fatally — callers should treat that
    as "user must run ``aethis login`` interactively". Diagnostic output is
    printed via ``console`` along the way.

    This is the re-usable kernel of the ``aethis login`` command, factored out
    so the lazy-auth helper can trigger the same flow on a 401 / first
    authenticated call.
    """
    from aethis_cli.auth import authenticate_with_clerk
    from aethis_cli.errors import AuthenticationError

    clerk_domain = os.environ.get("AETHIS_CLERK_DOMAIN", "clerk.aethis.ai")
    clerk_client_id = os.environ.get("AETHIS_CLERK_CLIENT_ID", "cwH009p1vPtyy1EG")

    info("Opening browser for sign-in...")
    console.print(f"Waiting for authentication ({timeout}s timeout)...\n")

    try:
        access_token = authenticate_with_clerk(clerk_domain, clerk_client_id, timeout)
    except AuthenticationError as e:
        console.print(f"[red]{e}[/red]")
        return None
    except OSError as e:
        console.print(f"[yellow]Could not open a browser ({e}).[/yellow]")
        return None

    success("Signed in.")

    import httpx

    info("Creating API key...")
    try:
        resp = httpx.post(
            f"{base_url}/api/v1/keys/",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "name": "cli-login",
                "scopes": [
                    "decide",
                    "projects:read",
                    "projects:write",
                    "rulesets:read",
                    "rulesets:explain",
                    "rulesets:write",
                ],
                "rate_limit_tier": "free",
            },
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        console.print(f"[yellow]Could not reach API at {base_url}: {e}[/yellow]")
        return None

    if resp.status_code != 201:
        console.print(f"[yellow]Key creation failed (HTTP {resp.status_code})[/yellow]")
        return None

    data = resp.json()
    full_key = data.get("full_key")
    if not full_key:
        console.print("[yellow]Unexpected API response.[/yellow]")
        return None

    save_api_key(full_key, profile=profile)
    return full_key


def _validate_key(api_key: str, base_url: str) -> bool:
    """Quick check that the key is accepted by the API."""
    import httpx

    try:
        resp = httpx.get(
            f"{base_url}/api/v1/public/projects/",
            headers={"X-API-Key": api_key},
            timeout=10.0,
        )
        return resp.status_code != 401
    except httpx.HTTPError:
        return True  # Network error — don't block save, let commands fail with context


def _prompt_manual_key(base_url: str, *, profile: Optional[str] = None) -> None:
    """Fall back to manual key entry."""
    console.print()
    console.print("Paste an API key from https://aethis.legal/dashboard/api-keys")
    api_key = typer.prompt("API key")
    if not api_key.strip():
        console.print("[red]No key entered.[/red]")
        raise typer.Exit(code=1)
    api_key = api_key.strip()
    if not _validate_key(api_key, base_url):
        console.print("[red]That key was rejected by the API (HTTP 401). Check it and try again.[/red]")
        raise typer.Exit(code=1)
    _save_key(api_key, profile=profile)


def login(
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        "-k",
        help="Paste an existing API key instead of browser sign-in.",
    ),
    timeout: int = typer.Option(120, "--timeout", help="Browser auth timeout in seconds"),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        help=(
            "Save the new key into a named profile slot instead of the active one. "
            "Useful for keeping admin and dev personas side-by-side."
        ),
    ),
) -> None:
    """Sign in and store an API key locally. First-time setup — this is all you need."""
    if profile == ANONYMOUS_PROFILE:
        console.print(
            f"[red]Cannot log in to the reserved '{ANONYMOUS_PROFILE}' profile — it always means 'use no key'.[/red]"
        )
        raise typer.Exit(code=1)

    base_url = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)
    if api_key:
        if not _validate_key(api_key, base_url):
            console.print("[red]That key was rejected by the API (HTTP 401). Check it and try again.[/red]")
            raise typer.Exit(code=1)
        _save_key(api_key, profile=profile)
        return

    full_key = run_browser_login(base_url, timeout=timeout, profile=profile)
    if full_key is None:
        # Browser flow failed at some step — fall through to manual paste so
        # the user can still complete login on a headless box.
        _prompt_manual_key(base_url, profile=profile)
        return

    success("Ready to use. Try: aethis status")
    info("Tip: run `aethis status` to verify, or `aethis account keys` to see all your keys.")
