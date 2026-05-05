"""aethis login — authenticate via browser or store an existing API key."""

from __future__ import annotations

import os
from typing import Optional

import typer
import yaml

from aethis_cli.config import DEFAULT_BASE_URL, credentials_path
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


def _save_to_file(api_key: str) -> None:
    """Fallback: store key in ~/.config/aethis/credentials (0600)."""
    creds = credentials_path()
    creds.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Atomic create with correct permissions — avoids TOCTOU race
    fd = os.open(str(creds), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(yaml.dump({"api_key": api_key}))


def save_api_key(api_key: str, *, announce: bool = True) -> None:
    """Save API key to the best available credential store.

    Public helper so the lazy-auth flow can persist a freshly minted key
    without going through the typer command. ``announce`` controls whether
    the success line is printed (the inline-login path prints its own).
    """
    if _save_to_keyring(api_key):
        if announce:
            success("API key saved to system keychain.")
    else:
        _save_to_file(api_key)
        if announce:
            info("keyring not available — key saved to file (install 'keyring' for OS keychain)")
            success(f"API key saved to {credentials_path()}")


# Backwards-compatible alias used internally by this module.
_save_key = save_api_key


def run_browser_login(base_url: str, timeout: int = 120) -> Optional[str]:
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

    clerk_domain = os.environ.get("AETHIS_CLERK_DOMAIN", "clerk.aethis.legal")
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

    save_api_key(full_key)
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


def _prompt_manual_key(base_url: str) -> None:
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
    _save_key(api_key)


def login(
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        "-k",
        help="Paste an existing API key instead of browser sign-in.",
    ),
    timeout: int = typer.Option(120, "--timeout", help="Browser auth timeout in seconds"),
) -> None:
    """Sign in and store an API key locally. First-time setup — this is all you need."""
    base_url = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)
    if api_key:
        if not _validate_key(api_key, base_url):
            console.print("[red]That key was rejected by the API (HTTP 401). Check it and try again.[/red]")
            raise typer.Exit(code=1)
        _save_key(api_key)
        return

    full_key = run_browser_login(base_url, timeout=timeout)
    if full_key is None:
        # Browser flow failed at some step — fall through to manual paste so
        # the user can still complete login on a headless box.
        _prompt_manual_key(base_url)
        return

    success("Ready to use. Try: aethis status")
    info("Tip: run `aethis status` to verify, or `aethis account keys` to see all your keys.")
