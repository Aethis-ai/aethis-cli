"""aethis explain — show human-readable rules."""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from aethis_cli.client import AethisClient
from aethis_cli.config import load_project_config, read_state, resolve_api_key
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel


def explain(
    bundle_id: Optional[str] = typer.Option(None, "--bundle-id", "-b"),
) -> None:
    """Show human-readable rules for a bundle."""
    cfg = load_project_config()
    api_key = resolve_api_key(cfg)
    client = AethisClient(api_key, cfg.base_url)

    if not bundle_id:
        state = read_state(cfg.config_path)
        bundle_id = state.get("bundle_id")
        if not bundle_id:
            console.print("[red]No bundle_id. Run 'aethis generate' first or pass --bundle-id.[/red]")
            raise typer.Exit(code=1)

    try:
        result = client.explain(bundle_id)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    table = Table(title=f"Rules — {bundle_id}")
    table.add_column("Group", style="cyan")
    table.add_column("Title")
    table.add_column("Rule")

    for c in result.get("criteria", []):
        table.add_row(c.get("group", ""), c["title"], c["rule_text"])

    console.print(table)
