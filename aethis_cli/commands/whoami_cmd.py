"""aethis whoami — show what this key can do."""

from __future__ import annotations

import typer

import os

from aethis_cli.client import AethisClient
from aethis_cli.config import DEFAULT_BASE_URL
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel


def _resolve_api_key_lax() -> tuple[str | None, str]:
    """Resolve an API key using the same fallback chain as other commands,
    but don't raise when no key is found — return (None, base_url) instead.
    """
    base_url = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)
    key = os.environ.get("AETHIS_API_KEY")
    if key:
        return key, base_url
    try:
        import keyring  # type: ignore[import-not-found]

        key = keyring.get_password("aethis-cli", "api_key")
        if key:
            return key, base_url
    except Exception:
        pass
    # Plaintext credentials file
    from pathlib import Path
    import yaml  # type: ignore[import-untyped]

    creds = Path.home() / ".config" / "aethis" / "credentials.yaml"
    if creds.exists():
        try:
            raw = yaml.safe_load(creds.read_text()) or {}
            key = raw.get("api_key")
            if key:
                return key, base_url
        except Exception:
            pass
    return None, base_url


def whoami() -> None:
    """Show the API key identity, scopes, tier, and whether authoring is available.

    Answers "can I author rules with this key?" before you try and get a 403.
    """
    api_key, base_url = _resolve_api_key_lax()
    if api_key is None:
        console.print(
            "[yellow]No Aethis API key configured.[/yellow]\n"
            "[dim]Set AETHIS_API_KEY in your environment, or run 'aethis login' "
            "to paste one. Decision tools work without a key.[/dim]"
        )
        raise typer.Exit(code=1)

    client = AethisClient(api_key, base_url)
    try:
        me = client.whoami()
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    console.print(f"[bold]Key:[/bold]         {me.get('key_id')}")
    console.print(f"[bold]Tenant:[/bold]      {me.get('tenant_id')}")
    console.print(f"[bold]Tier:[/bold]        {me.get('rate_limit_tier')}")
    scopes = me.get("scopes") or []
    scopes_str = ", ".join(sorted(scopes)) if scopes else "(none)"
    console.print(f"[bold]Scopes:[/bold]      {scopes_str}")
    if me.get("can_author"):
        console.print("[green]✓ Authoring enabled[/green] — you can create and publish rulesets.")
    else:
        console.print("[yellow]✗ Authoring not available[/yellow] — this key can only evaluate rulesets.")
        console.print(
            "[dim]Rule authoring is invite-only private beta. Request access at https://aethis.ai/developer-access[/dim]"
        )
