"""aethis usage — show your per-operation-class rate-limit budget (epic #552)."""

from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich.table import Table

from aethis_cli.auth_helpers import resolve_cached_key
from aethis_cli.client import AethisClient
from aethis_cli.config import resolve_base_url_with_source
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel
from aethis_cli.render import emit, is_json_requested


def _fmt_reset(ts: object) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%H:%M UTC")  # type: ignore[arg-type]
    except (ValueError, TypeError, OSError):
        return "-"


def usage() -> None:
    """Show your rate-limit budget per operation class (rolling 24h window).

    Each class — decide / generate / author / read / keys / admin — has its own
    budget. `generate` (LLM rule generation) is the scarce one; browsing and
    status polling (`read`) are effectively unlimited. Check here before a big
    authoring run so a 429 is never the first signal.
    """
    api_key = resolve_cached_key()
    base_url, _ = resolve_base_url_with_source()
    if api_key is None:
        console.print(
            "[yellow]No Aethis API key configured.[/yellow]\n[dim]Run 'aethis login' or set AETHIS_API_KEY.[/dim]"
        )
        raise typer.Exit(code=1)

    client = AethisClient(api_key, base_url)
    try:
        data = client.usage()
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    if is_json_requested():
        emit(data)
        return

    console.print(f"[bold]Tier:[/bold] {data.get('tier')}")
    table = Table(title="Rate-limit budget (rolling 24h)")
    table.add_column("Class", style="bold")
    table.add_column("Used", justify="right")
    table.add_column("Limit", justify="right")
    table.add_column("Remaining", justify="right")
    table.add_column("Resets", justify="right")
    for c in data.get("classes", []):
        rem = c.get("remaining", 0)
        rem_style = "green" if rem > 0 else "red"
        table.add_row(
            str(c.get("class", "")),
            str(c.get("used", 0)),
            str(c.get("limit", 0)),
            f"[{rem_style}]{rem}[/{rem_style}]",
            _fmt_reset(c.get("reset")),
        )
    console.print(table)
