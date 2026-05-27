"""aethis explain — show human-readable rules."""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from aethis_cli.commands._id_utils import require_ruleset_id
from aethis_cli.config import load_client_or_anon, read_state
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel
from aethis_cli.render import emit, is_json_requested


def explain(
    ruleset_id: Optional[str] = typer.Option(
        None,
        "--ruleset-id",
        "-b",
        help=(
            "Ruleset ID (the 'Ruleset' column from `aethis projects list`, "
            "e.g. example_ruleset:20260408-abc1234). Not the `proj_*` Project ID. "
            "Defaults to .aethis/state.json if omitted."
        ),
    ),
) -> None:
    """Show human-readable rules for a ruleset. No API key required for public rulesets.

    Examples:

        aethis explain -b aethis/uk-settlement-continuous-residence
        aethis explain -b crew_certification:20260408-cbf63f1f
        aethis explain                   # uses .aethis/state.json if present
    """
    cfg, client = load_client_or_anon()

    if not ruleset_id:
        state = read_state(cfg.config_path)
        ruleset_id = state.get("ruleset_id")
        if not ruleset_id:
            console.print(
                "[red]No ruleset_id.[/red] Pass --ruleset-id or run from a project "
                "directory where `aethis generate`/`publish` has been run."
            )
            raise typer.Exit(code=1)

    require_ruleset_id(ruleset_id)

    try:
        result = client.explain(ruleset_id)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    criteria = result.get("criteria", [])

    if is_json_requested():
        emit(criteria)
        return

    def _build_rules_table() -> Table:
        table = Table(title=f"Rules — {ruleset_id}")
        table.add_column("Group", style="cyan")
        table.add_column("Title")
        table.add_column("Rule")
        for c in criteria:
            table.add_row(c.get("group", ""), c["title"], c["rule_text"])
        return table

    emit(criteria, table=_build_rules_table)
