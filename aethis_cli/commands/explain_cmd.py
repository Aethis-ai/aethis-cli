"""aethis explain — show human-readable rules."""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from aethis_cli.commands._id_utils import require_bundle_id
from aethis_cli.config import load_client_or_fallback, read_state
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel


def explain(
    bundle_id: Optional[str] = typer.Option(
        None,
        "--bundle-id",
        "-b",
        help=(
            "Bundle ID (the 'Bundle' column from `aethis projects list`, "
            "e.g. example_bundle:20260408-abc1234). Not the `proj_*` Project ID. "
            "Defaults to .aethis/state.json if omitted."
        ),
    ),
) -> None:
    """Show human-readable rules for a bundle.

    Examples:

        aethis explain -b crew_certification:20260408-cbf63f1f
        aethis explain                   # uses .aethis/state.json if present
        aethis --base-url http://localhost:8080 explain -b my_bundle:20260401-a1b2c3d
    """
    cfg, client = load_client_or_fallback()

    if not bundle_id:
        state = read_state(cfg.config_path)
        bundle_id = state.get("bundle_id")
        if not bundle_id:
            console.print(
                "[red]No bundle_id.[/red] Pass --bundle-id or run from a project "
                "directory where `aethis generate`/`publish` has been run."
            )
            raise typer.Exit(code=1)

    require_bundle_id(bundle_id)

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
