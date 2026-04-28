"""aethis fields — show field schema for a bundle."""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from aethis_cli.config import load_client_or_fallback, read_state
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel


def fields(
    bundle_id: Optional[str] = typer.Option(None, "--bundle-id", "-b"),
) -> None:
    """Show the input fields expected by a bundle."""
    cfg, client = load_client_or_fallback()

    if not bundle_id:
        state = read_state(cfg.config_path)
        bundle_id = state.get("bundle_id")
        if not bundle_id:
            console.print("[red]No bundle_id. Run 'aethis generate' first or pass --bundle-id.[/red]")
            raise typer.Exit(code=1)

    try:
        result = client.get_schema(bundle_id)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    table = Table(title=f"Fields — {bundle_id}")
    table.add_column("Field ID", style="cyan")
    table.add_column("Type")
    table.add_column("Description")
    table.add_column("Enum values")

    for f in result.get("fields", []):
        enum_vals = ", ".join(f["enum_values"]) if f.get("enum_values") else ""
        table.add_row(f["field_id"], f["field_type"], f.get("description") or "", enum_vals)

    console.print(table)
