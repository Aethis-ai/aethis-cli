"""aethis account — manage API keys via browser-based Clerk sign-in."""

from __future__ import annotations

import os
from typing import List, Optional

import httpx
import typer

from aethis_cli.auth import authenticate_with_clerk
from aethis_cli.commands.login_cmd import save_api_key
from aethis_cli.config import DEFAULT_BASE_URL
from aethis_cli.errors import AuthenticationError
from aethis_cli.output import console, info, success
from aethis_cli.render import emit, is_json_requested

CLERK_DOMAIN = os.environ.get("AETHIS_CLERK_DOMAIN", "clerk.aethis.ai")
CLERK_CLIENT_ID = os.environ.get("AETHIS_CLERK_CLIENT_ID", "gEiHOxoeLgZJifjf")

VALID_SCOPES = {
    "decide",
    "rulesets:read",
    "rulesets:explain",
    "rulesets:write",
    "keys:manage",
    "projects:read",
    "projects:write",
    "rulebooks:read",
    "rulebooks:write",
}
VALID_TIERS = {"free", "starter", "pro"}
DEFAULT_SCOPES = ["decide", "projects:read", "projects:write", "rulesets:read", "rulesets:explain", "rulesets:write"]

account_app = typer.Typer(
    name="account",
    help="Manage your Aethis account and API keys (browser sign-in).",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _format_api_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return resp.text

    detail = data.get("detail") if isinstance(data, dict) else data
    if isinstance(detail, dict):
        reason = detail.get("reason_code", "unknown")
        action = detail.get("action", "unknown")
        missing = detail.get("missing_permissions", [])
        missing_str = ", ".join(missing) if isinstance(missing, list) else str(missing)
        msg = detail.get("message") or detail.get("error") or "Request denied"
        return f"{msg} (reason={reason}, action={action}, missing={missing_str})"
    if isinstance(detail, str):
        return detail
    return str(detail)


def _fetch_permissions(base_url: str) -> tuple[list[dict], set[str]]:
    try:
        resp = httpx.get(f"{base_url}/api/v1/public/permissions", timeout=10.0)
    except httpx.HTTPError:
        return [], set(VALID_SCOPES)

    if resp.status_code != 200:
        return [], set(VALID_SCOPES)

    try:
        items = resp.json()
    except Exception:
        return [], set(VALID_SCOPES)

    if not isinstance(items, list):
        return [], set(VALID_SCOPES)

    permissions: set[str] = set()
    parsed_items: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        req = item.get("required_permissions", [])
        if isinstance(req, list):
            for p in req:
                if isinstance(p, str) and p:
                    permissions.add(p)
        parsed_items.append(item)

    if not permissions:
        permissions = set(VALID_SCOPES)
    return parsed_items, permissions


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
    no_save: bool = typer.Option(False, "--no-save", help="Print key but don't save"),
    timeout: int = typer.Option(120, "--timeout", help="Browser auth timeout in seconds"),
) -> None:
    """Mint an additional API key (for rotation, multi-machine, or scoped access). For first-time setup use `aethis login` instead."""
    base_url = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)
    if scopes is None:
        scopes = list(DEFAULT_SCOPES)

    _, available_permissions = _fetch_permissions(base_url)

    # Validate inputs
    invalid_scopes = set(scopes) - available_permissions
    if invalid_scopes:
        console.print(f"[red]Invalid scope(s): {', '.join(invalid_scopes)}[/red]")
        console.print(f"Valid scopes: {', '.join(sorted(available_permissions))}")
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
        console.print(f"[red]Key creation failed (HTTP {resp.status_code}): {_format_api_error(resp)}[/red]")
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
        save_api_key(full_key)


@account_app.command()
def keys(
    timeout: int = typer.Option(120, "--timeout", help="Browser auth timeout in seconds"),
) -> None:
    """List your API keys (requires browser sign-in)."""
    base_url = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)
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
        console.print(f"[red]Failed to list keys (HTTP {resp.status_code}): {_format_api_error(resp)}[/red]")
        raise typer.Exit(code=1)

    data = resp.json()
    if not data:
        if is_json_requested():
            emit([])
        else:
            info("No API keys found.")
        return

    from rich.table import Table

    def _build_keys_table() -> Table:
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
        return table

    emit(data, table=_build_keys_table)


@account_app.command()
def revoke(
    key_id: str = typer.Argument(..., help="Key ID to revoke (ak_...)"),
    timeout: int = typer.Option(120, "--timeout", help="Browser auth timeout in seconds"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Revoke an API key (requires browser sign-in)."""
    base_url = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)
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
        console.print(f"[red]Revoke failed (HTTP {resp.status_code}): {_format_api_error(resp)}[/red]")
        raise typer.Exit(code=1)
