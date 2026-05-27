"""aethis whoami — show what this key can do."""

from __future__ import annotations

import typer

from aethis_cli.auth_helpers import resolve_cached_key
from aethis_cli.client import AethisClient
from aethis_cli.config import resolve_base_url_with_source
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel


def whoami() -> None:
    """Show the API key identity, scopes, tier, and whether authoring is available.

    Answers "can I author rules with this key?" before you try and get a 403.
    """
    api_key = resolve_cached_key()
    base_url, _ = resolve_base_url_with_source()
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
