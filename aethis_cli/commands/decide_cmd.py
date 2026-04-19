"""aethis decide — quick eligibility check."""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.table import Table

from aethis_cli.commands._id_utils import require_bundle_id
from aethis_cli.config import load_client_or_fallback, read_state
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel


def decide(
    input_json: str = typer.Option(..., "--input", "-i", help="JSON object of field values"),
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
    explain: bool = typer.Option(False, "--explain", "-e", help="Show reasoning for the decision"),
) -> None:
    """Evaluate eligibility against a published bundle.

    Examples:

        aethis decide -b my_bundle:20260401-a1b2c3d -i '{"age": 21, "country": "UK"}'
        aethis decide -b my_bundle:20260401-a1b2c3d -i @inputs.json --explain
        aethis decide -i '{...}'         # uses bundle from .aethis/state.json

    Input is a JSON object mapping field IDs to values. Use --explain to see the
    reasoning trace (which rules fired, which group satisfied the decision).
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
        field_values = json.loads(input_json)
    except json.JSONDecodeError as e:
        console.print(f"[red]Invalid JSON: {e}[/red]")
        raise typer.Exit(code=1)

    if not isinstance(field_values, dict):
        console.print("[red]Input must be a JSON object, not a list or scalar.[/red]")
        raise typer.Exit(code=1)

    opts = {}
    if explain:
        opts["include_trace"] = True
        opts["include_explanation"] = True

    try:
        result = client.decide(bundle_id, field_values, **opts)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    decision = result["decision"]
    color = {"eligible": "green", "not_eligible": "red"}.get(decision, "yellow")
    console.print(f"\nDecision: [bold {color}]{decision}[/bold {color}]")
    console.print(f"Bundle:   {result.get('bundle_id')}")
    console.print(f"Fields:   {result.get('fields_provided')}/{result.get('fields_evaluated')} provided")
    if result.get("missing_fields"):
        console.print(f"Missing:  {', '.join(result['missing_fields'])}")
    if result.get("field_errors"):
        for fid, err in result["field_errors"].items():
            console.print(f"  [red]! {fid}: {err}[/red]")

    nq = result.get("next_question")
    if nq:
        console.print(f"\nNext question: [bold]{nq['question']}[/bold]  ({nq['field_id']}, weight={nq['weight']})")
    path = result.get("optimal_path")
    if path:
        console.print(f"Remaining:    {len(path)} questions (total weight={sum(q['weight'] for q in path)})")

    if explain and result.get("trace"):
        _print_trace(result["trace"])
    if explain and result.get("explanation"):
        _print_explanation(result["explanation"])


_STATUS_ICONS = {
    "satisfied": "[green]PASS[/green]",
    "not_satisfied": "[red]FAIL[/red]",
    "pending": "[yellow]PENDING[/yellow]",
}
# Map API response values to display labels
_API_STATUS_MAP = {"SAT": "satisfied", "UNSAT": "not_satisfied", "UNKNOWN": "pending"}


def _print_trace(trace: dict) -> None:
    """Print per-group and per-requirement evaluation results."""
    console.print("\n[bold]Reasoning[/bold]")

    if trace.get("path"):
        console.print(f"  Satisfied by: [green]{trace['path']}[/green]")

    groups = trace.get("group_statuses")
    if groups:
        for group, status in groups.items():
            mapped = _API_STATUS_MAP.get(status, status)
            icon = _STATUS_ICONS.get(mapped, f"[yellow]{mapped}[/yellow]")
            console.print(f"  {icon}  {group}")

    reqs = trace.get("requirements")
    if isinstance(reqs, list):
        for req in reqs:
            status = req.get("status", "")
            title = req.get("title") or req.get("id", "")
            icon = {"satisfied": "[green]\u2713[/green]", "not_satisfied": "[red]\u2717[/red]"}.get(
                status, "[yellow]?[/yellow]"
            )
            console.print(f"  {icon} {title}")
            if req.get("reason"):
                console.print(f"    {req['reason']}")
    elif isinstance(reqs, dict):
        for req_id, info in reqs.items():
            if isinstance(info, dict):
                status = info.get("status", "")
                icon = {"satisfied": "[green]\u2713[/green]", "not_satisfied": "[red]\u2717[/red]"}.get(
                    status, "[yellow]?[/yellow]"
                )
                console.print(f"  {icon} {req_id}")
                if info.get("reason"):
                    console.print(f"    {info['reason']}")
            else:
                console.print(f"  {req_id}: {info}")

    if trace.get("satisfied_requirement"):
        console.print(f"\n  Satisfied by: [green]{trace['satisfied_requirement']}[/green]")

    if trace.get("failing_requirements"):
        console.print("\n  Failing:")
        for fr in trace["failing_requirements"]:
            if isinstance(fr, dict):
                console.print(f"    [red]\u2717[/red] {fr.get('title', fr.get('id', ''))}")
            else:
                console.print(f"    [red]\u2717[/red] {fr}")


def _print_explanation(explanation: list) -> None:
    """Print human-readable rule text in a table."""
    console.print("\n[bold]Rules[/bold]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Group")
    table.add_column("Rule")
    table.add_column("Requirement")

    for rule in explanation:
        table.add_row(
            rule.get("group", ""),
            rule.get("title", ""),
            rule.get("rule_text", ""),
        )

    console.print(table)
