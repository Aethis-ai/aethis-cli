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


def _save_key(api_key: str) -> None:
    """Save API key to the best available credential store."""
    if _save_to_keyring(api_key):
        success("API key saved to system keychain.")
    else:
        _save_to_file(api_key)
        info("keyring not available — key saved to file (install 'keyring' for OS keychain)")
        success(f"API key saved to {credentials_path()}")


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
        None, "--api-key", "-k",
        help="Paste an existing API key instead of browser sign-in.",
    ),
    timeout: int = typer.Option(120, "--timeout", help="Browser auth timeout in seconds"),
    base_url: str = typer.Option(
        os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL),
        "--base-url", help="API base URL",
    ),
) -> None:
    """Authenticate with Aethis. Opens your browser to sign in and create an API key."""
    if api_key:
        if not _validate_key(api_key, base_url):
            console.print("[red]That key was rejected by the API (HTTP 401). Check it and try again.[/red]")
            raise typer.Exit(code=1)
        _save_key(api_key)
        return

    # Browser-based OAuth flow
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
        _prompt_manual_key(base_url)
        return

    success("Signed in.")

    # Create an API key automatically
    import httpx

    info("Creating API key...")
    try:
        resp = httpx.post(
            f"{base_url}/api/v1/keys/",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "name": "cli-login",
                "scopes": ["decide", "projects:write", "bundles:read", "bundles:explain", "bundles:write"],
                "rate_limit_tier": "free",
            },
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        console.print(f"[yellow]Could not reach API at {base_url}: {e}[/yellow]")
        console.print("Browser sign-in succeeded, but key creation failed.")
        _prompt_manual_key(base_url)
        return

    if resp.status_code != 201:
        console.print(f"[yellow]Key creation failed (HTTP {resp.status_code})[/yellow]")
        console.print("Browser sign-in succeeded, but key creation failed.")
        _prompt_manual_key(base_url)
        return

    data = resp.json()
    full_key = data.get("full_key")
    if not full_key:
        console.print("[yellow]Unexpected API response.[/yellow]")
        _prompt_manual_key(base_url)
        return

    _save_key(full_key)
    success("Ready to use. Try: aethis status")
