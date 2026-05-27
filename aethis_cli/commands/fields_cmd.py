"""aethis fields — show field schema for a ruleset."""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from aethis_cli.config import load_client_or_anon, read_state
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel
from aethis_cli.render import emit, is_json_requested


def fields(
    ruleset_id: Optional[str] = typer.Option(
        None,
        "--ruleset-id",
        "-b",
        help=(
            "Ruleset ID or slug (e.g. aethis/uk-settlement or "
            "my_ruleset:20260408-abc1234). Defaults to .aethis/state.json if omitted."
        ),
    ),
) -> None:
    """Show the input fields expected by a ruleset. No API key required for public rulesets.

    Examples:

        aethis fields -b aethis/uk-settlement-continuous-residence
        aethis fields -b crew_certification:20260408-cbf63f1f
        aethis fields                    # uses .aethis/state.json if present
    """
    cfg, client = load_client_or_anon()

    if not ruleset_id:
        state = read_state(cfg.config_path)
        ruleset_id = state.get("ruleset_id")
        if not ruleset_id:
            console.print("[red]No ruleset_id. Run 'aethis generate' first or pass --ruleset-id.[/red]")
            raise typer.Exit(code=1)

    try:
        result = client.get_schema(ruleset_id)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    field_specs = result.get("fields", [])

    if is_json_requested():
        emit(field_specs)
        return

    def _build_fields_table() -> Table:
        table = Table(title=f"Fields — {ruleset_id}")
        table.add_column("Field ID", style="cyan")
        table.add_column("Type")
        table.add_column("Description")
        table.add_column("Enum values")
        for f in field_specs:
            enum_vals = ", ".join(f["enum_values"]) if f.get("enum_values") else ""
            table.add_row(f["field_id"], f["field_type"], f.get("description") or "", enum_vals)
        return table

    emit(field_specs, table=_build_fields_table)
