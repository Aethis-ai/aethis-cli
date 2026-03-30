"""aethis account — manage API keys via browser-based Clerk sign-in."""

from __future__ import annotations

import os
from typing import List, Optional

import httpx
import typer

from aethis_cli.auth import authenticate_with_clerk
from aethis_cli.commands.login_cmd import _save_to_keyring, _save_to_file
from aethis_cli.config import DEFAULT_BASE_URL
from aethis_cli.errors import AuthenticationError
from aethis_cli.output import console, error_panel, info, success

_BASE_URL = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)

CLERK_DOMAIN = os.environ.get("AETHIS_CLERK_DOMAIN", "clerk.aethis.legal")
CLERK_CLIENT_ID = os.environ.get("AETHIS_CLERK_CLIENT_ID", "cwH009p1vPtyy1EG")

VALID_SCOPES = {"decide", "bundles:read", "bundles:explain", "bundles:write", "keys:manage", "projects:write"}
VALID_TIERS = {"free", "starter", "pro"}

account_app = typer.Typer(
    name="account",
    help="Manage your Aethis account and API keys (browser sign-in).",
    no_args_is_help=True,
)


def _get_clerk_config() -> tuple[str, str]:
    """Return (domain, client_id), raising if not configured."""
    domain = CLERK_DOMAIN
    client_id = CLERK_CLIENT_ID
    if not client_id:
        console.print(
            "[red]Clerk OAuth client_id not configured.[/red]\n"
            "Set AETHIS_CLERK_CLIENT_ID environment variable.\n"
            "Or use 'aethis login' to paste an existing API key."
        )
        raise typer.Exit(code=1)
    return domain, client_id


def _clerk_auth(timeout: int) -> str:
    """Run Clerk OAuth flow, return access token."""
    domain, client_id = _get_clerk_config()
    info("Opening browser for sign-in...")
    console.print(f"Waiting for authentication ({timeout}s timeout)...\n")
    try:
        return authenticate_with_clerk(domain, client_id, timeout)
    except AuthenticationError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None


@account_app.command()
def generate(
    name: str = typer.Option("cli-generated", "--name", "-n", help="Key name"),
    scopes: Optional[List[str]] = typer.Option(None, "--scope", "-s", help="Key scopes (repeatable)"),
    tier: str = typer.Option("free", "--tier", "-t", help="Rate limit tier: free|starter|pro"),
    base_url: str = typer.Option(_BASE_URL, "--base-url", help="API base URL"),
    no_save: bool = typer.Option(False, "--no-save", help="Print key but don't save"),
    timeout: int = typer.Option(120, "--timeout", help="Browser auth timeout in seconds"),
) -> None:
    """Create a new API key by signing in through your browser."""
    if scopes is None:
        scopes = ["decide", "projects:write", "bundles:read", "bundles:explain", "bundles:write"]

    # Validate inputs
    invalid_scopes = set(scopes) - VALID_SCOPES
    if invalid_scopes:
        console.print(f"[red]Invalid scope(s): {', '.join(invalid_scopes)}[/red]")
        console.print(f"Valid scopes: {', '.join(sorted(VALID_SCOPES))}")
        raise typer.Exit(code=1)

    if tier not in VALID_TIERS:
        console.print(f"[red]Invalid tier: {tier}[/red]. Must be one of: {', '.join(sorted(VALID_TIERS))}")
        raise typer.Exit(code=1)

    access_token = _clerk_auth(timeout)
    success("Authenticated successfully.")

    # Create API key via the key management endpoint
    info("Creating API key...")
    try:
        resp = httpx.post(
            f"{base_url}/api/v1/keys/",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"name": name, "scopes": scopes, "rate_limit_tier": tier},
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        console.print(f"[red]Could not reach API at {base_url}: {e}[/red]")
        raise typer.Exit(code=1) from None

    if resp.status_code != 201:
        console.print(f"[red]Key creation failed (HTTP {resp.status_code}): {resp.text}[/red]")
        raise typer.Exit(code=1)

    data = resp.json()
    full_key = data.get("full_key")
    if not full_key:
        console.print("[red]Unexpected API response: missing 'full_key'.[/red]")
        raise typer.Exit(code=1)

    console.print()
    success("API key created:")
    console.print(f"  Key ID:   {data.get('key_id', 'unknown')}")
    console.print(f"  Name:     {data.get('name', name)}")
    console.print(f"  Scopes:   {', '.join(data.get('scopes', scopes))}")
    console.print(f"  Tier:     {data.get('rate_limit_tier', tier)}")
    console.print()
    console.print("[bold yellow]Full key (shown once only):[/bold yellow]")
    console.print(f"  {full_key}")
    console.print()

    if no_save:
        info("--no-save specified. Key not saved to credential store.")
    else:
        if _save_to_keyring(full_key):
            success("API key saved to system keychain.")
        else:
            _save_to_file(full_key)
            success("API key saved to credentials file.")


@account_app.command()
def keys(
    base_url: str = typer.Option(_BASE_URL, "--base-url", help="API base URL"),
    timeout: int = typer.Option(120, "--timeout", help="Browser auth timeout in seconds"),
) -> None:
    """List your API keys (requires browser sign-in)."""
    access_token = _clerk_auth(timeout)
    success("Authenticated successfully.")

    try:
        resp = httpx.get(
            f"{base_url}/api/v1/keys/",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        console.print(f"[red]Could not reach API at {base_url}: {e}[/red]")
        raise typer.Exit(code=1) from None

    if resp.status_code != 200:
        console.print(f"[red]Failed to list keys (HTTP {resp.status_code}): {resp.text}[/red]")
        raise typer.Exit(code=1)

    data = resp.json()
    if not data:
        info("No API keys found.")
        return

    from rich.table import Table

    table = Table(title="API Keys")
    table.add_column("Key ID", style="bold")
    table.add_column("Name")
    table.add_column("Scopes")
    table.add_column("Tier")
    table.add_column("Created")
    table.add_column("Revoked")

    for key in data:
        table.add_row(
            key.get("key_id", ""),
            key.get("name", ""),
            ", ".join(key.get("scopes", [])),
            key.get("rate_limit_tier", ""),
            key.get("created_at", "")[:10] if key.get("created_at") else "",
            "yes" if key.get("revoked") else "",
        )

    console.print(table)


@account_app.command()
def revoke(
    key_id: str = typer.Argument(..., help="Key ID to revoke (ak_...)"),
    base_url: str = typer.Option(_BASE_URL, "--base-url", help="API base URL"),
    timeout: int = typer.Option(120, "--timeout", help="Browser auth timeout in seconds"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Revoke an API key (requires browser sign-in)."""
    if not yes:
        confirmed = typer.confirm(f"Revoke key {key_id}? This cannot be undone")
        if not confirmed:
            raise typer.Abort()

    access_token = _clerk_auth(timeout)
    success("Authenticated successfully.")

    try:
        resp = httpx.delete(
            f"{base_url}/api/v1/keys/{key_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        console.print(f"[red]Could not reach API at {base_url}: {e}[/red]")
        raise typer.Exit(code=1) from None

    if resp.status_code == 204:
        success(f"Key {key_id} revoked.")
    elif resp.status_code == 404:
        console.print(f"[red]Key {key_id} not found.[/red]")
        raise typer.Exit(code=1)
    else:
        console.print(f"[red]Revoke failed (HTTP {resp.status_code}): {resp.text}[/red]")
        raise typer.Exit(code=1)
