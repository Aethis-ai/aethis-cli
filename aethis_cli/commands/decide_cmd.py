"""aethis decide — quick eligibility check."""

from __future__ import annotations

import json
from typing import Optional

import typer

from aethis_cli.commands._id_utils import require_ruleset_id
from aethis_cli.config import load_client_or_anon, read_state
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel


def decide(
    input_json: str = typer.Option(..., "--input", "-i", help="JSON object of field values"),
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
    explain: bool = typer.Option(False, "--explain", "-e", help="Show reasoning for the decision"),
) -> None:
    """Evaluate eligibility against a published ruleset. No API key required for public rulesets.

    Examples:

        aethis decide -b aethis/uk-settlement-continuous-residence -i '{"days_outside_uk": 50}'
        aethis decide -b my_ruleset:20260401-a1b2c3d -i '{"age": 21, "country": "UK"}'
        aethis decide -b my_ruleset:20260401-a1b2c3d --input @inputs.json --explain
        aethis decide -i '{...}'         # uses ruleset from .aethis/state.json

    Input is a JSON object mapping field IDs to values. Use `aethis fields -b <ruleset>`
    to see which fields are available. Use --explain to see the reasoning trace.
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
        result = client.decide(ruleset_id, field_values, **opts)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    decision = result["decision"]
    color = {"eligible": "green", "not_eligible": "red"}.get(decision, "yellow")
    console.print(f"\nDecision: [bold {color}]{decision}[/bold {color}]")
    console.print(f"Ruleset:   {result.get('ruleset_id')}")
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


_CRITERION_ICONS = {
    "satisfied": "[green]✓[/green]",
    "not_satisfied": "[red]✗[/red]",
    "pending": "[yellow]?[/yellow]",
}


def _print_explanation(explanation: dict) -> None:
    """Print the layered decision explanation.

    Shape (see aethis-core public decide route): `{decision, decision_path?,
    groups: [{group, status, criteria: [{criterion_id, title, status,
    supporting_facts?, source_refs?}]}], unused_facts}`.
    """
    console.print("\n[bold]Rules[/bold]")

    path = explanation.get("decision_path")
    if path:
        console.print(f"  Satisfied by: [green]{path}[/green]")

    for group in explanation.get("groups", []) or []:
        name = group.get("group", "")
        status = group.get("status", "")
        icon = _STATUS_ICONS.get(status, f"[yellow]{status}[/yellow]")
        console.print(f"\n  [bold]{name}[/bold] {icon}")

        for crit in group.get("criteria", []) or []:
            cstatus = crit.get("status", "")
            cicon = _CRITERION_ICONS.get(cstatus, "[yellow]?[/yellow]")
            title = crit.get("title") or crit.get("criterion_id", "")
            cid = crit.get("criterion_id", "")
            suffix = f" [dim]({cid})[/dim]" if cid and cid != title else ""
            console.print(f"    {cicon} {title}{suffix}")
            for fact in crit.get("supporting_facts", []) or []:
                if isinstance(fact, dict):
                    console.print(
                        f"        [dim]{fact.get('field', '')} = {fact.get('value', '')}[/dim]"
                    )

    unused = explanation.get("unused_facts") or []
    if unused:
        console.print(
            "\n  [dim]Unused fields (provided but not referenced by any "
            "satisfied criterion):[/dim]"
        )
        for field in unused:
            console.print(f"    [dim]- {field}[/dim]")
