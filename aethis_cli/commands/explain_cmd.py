"""aethis explain — show human-readable rules."""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from aethis_cli import contract
from aethis_cli.commands._id_utils import require_ruleset_id
from aethis_cli.config import load_client_or_anon, read_state
from aethis_cli.decision_view import print_identity, print_sources
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
    """Show human-readable rules for a ruleset. No API key required.

    Reading rules is open to everyone: `explain`, `fields` and `decide` call
    public endpoints and need no account and no key. Authoring rulesets is
    invite-only -- see `aethis login --help`.

    Output carries the ruleset's immutable identity (id, published version,
    content digest) and, where the ruleset publishes them, the validated
    source references behind each criterion.

    Examples:

        aethis explain -b aethis/spacecraft-crew-certification
        aethis explain -b crew_certification:20260408-cbf63f1f
        aethis --output json explain -b aethis/spacecraft-crew-certification   # full envelope
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

    criteria = result.get("criteria", []) if isinstance(result, dict) else []

    if is_json_requested():
        # The whole envelope, not just the criteria array: the immutable
        # identity (ruleset_id / ruleset_version / content_digest) is what
        # makes a machine-consumed explanation reproducible, and dropping it
        # here would strip provenance on exactly the path that needs it most.
        emit(result)
        return

    print_identity(result)

    def _build_rules_table() -> Table:
        table = Table(title=f"Rules — {ruleset_id}")
        table.add_column("Group", style="cyan")
        table.add_column("Title")
        table.add_column("Rule")
        table.add_column("Sources", justify="right")
        for c in criteria:
            refs = c.get("source_references") or []
            table.add_row(
                c.get("group", "") or "",
                c.get("title", "") or "",
                c.get("rule_text", "") or "",
                str(len(refs)) if refs else "-",
            )
        return table

    console.print("\n[bold]Logic[/bold]")
    emit(criteria, table=_build_rules_table)

    print_sources(
        contract.iter_criteria_sources(criteria),
        empty_note=(
            "No published source references for this ruleset. Citations are "
            "attached when a ruleset is published under the source-reference "
            "contract."
        ),
        base_url=getattr(client, "base_url", None),
    )
