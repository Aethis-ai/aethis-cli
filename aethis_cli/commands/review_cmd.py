"""aethis review — the Authoring Coach report for a project.

Thin CLI over ``POST /api/v1/public/projects/{id}/review``. The deterministic
report (objective checks, a reproducible score, the single author-actionable
``next_skill``) needs only your API key. ``--coach`` opts into LLM mentoring
prose on your own Anthropic key.
"""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.table import Table

from aethis_cli.config import (
    ProjectConfig,
    load_project_config,
    make_authed_client,
    resolve_anthropic_key,
    resolve_api_key,
    resolve_base_url_with_source,
)
from aethis_cli.errors import AethisAPIError, ConfigError
from aethis_cli.output import console, error_panel, warn
from aethis_cli.render import is_json_requested


def review(
    project_id: Optional[str] = typer.Argument(
        None,
        help="Project ID (proj_…). Defaults to the current project in .aethis/state.json.",
    ),
    coach: bool = typer.Option(
        False,
        "--coach",
        help="Add LLM coaching prose (opt-in, billed to your own Anthropic key via ANTHROPIC_API_KEY).",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw ReviewReport as JSON (pipe-friendly).",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show the full per-check table, not just the headline + next step.",
    ),
) -> None:
    """Review a project against the Authoring Coach rubric.

    Prints an authoring score, a couple of evidence-cited strengths, and the
    single highest-leverage next improvement. Advisory only — the exit code is
    always 0 regardless of score.

    Examples:

        aethis review                       # review the current project dir
        aethis review proj_abc123           # review a specific project
        aethis review --coach               # add LLM mentoring prose
        aethis review --json | jq .score    # machine-readable report
        aethis review --verbose             # full per-check breakdown
    """
    try:
        cfg = load_project_config()
    except ConfigError:
        # Allow reviewing any project by id from outside a project dir.
        if not project_id:
            console.print(
                "[red]No project.[/red] Pass a project id (proj_…) or run from a "
                "project directory where `aethis generate` has been run."
            )
            raise typer.Exit(code=1)
        base_url, _ = resolve_base_url_with_source()
        cfg = ProjectConfig(project="", base_url=base_url)

    pid = project_id or cfg.project_id
    if not pid:
        console.print("[red]No project id.[/red] Pass a project id, or run `aethis generate` in this directory first.")
        raise typer.Exit(code=1)

    try:
        api_key = resolve_api_key(cfg)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    anthropic_key = resolve_anthropic_key(cfg) if coach else None
    if coach and not anthropic_key:
        warn(
            "--coach needs an Anthropic key (set ANTHROPIC_API_KEY). Proceeding: "
            "internal keys use the server key; external keys will get a 400."
        )

    client = make_authed_client(api_key, cfg.base_url, anthropic_key=anthropic_key)

    try:
        report = client.review(pid, coach=coach)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    if json_out or is_json_requested():
        # Raw ReviewReport — plain `print` so pipes see clean bytes, no ANSI.
        print(json.dumps(report, indent=2, default=str))
        return

    _render_report(report, verbose=verbose)


def _score_colour(score: Optional[int]) -> str:
    """Green ≥80, yellow ≥50, red below — a plain heuristic for the headline."""
    if score is None:
        return "yellow"
    if score >= 80:
        return "green"
    if score >= 50:
        return "yellow"
    return "red"


def _render_report(report: dict, *, verbose: bool) -> None:
    """Human render: headline · strengths · the single next step · (optional) table."""
    score = report.get("score")
    completeness = report.get("data_completeness", "ok")
    strengths = report.get("strengths") or []
    next_skill = report.get("next_skill")
    coaching = report.get("coaching")
    checks = report.get("checks") or []

    # Headline — score, with a friendly empty state for thin projects.
    if completeness == "thin":
        console.print("\n[bold]Authoring review[/bold] — [yellow]just getting started[/yellow]")
        console.print("[dim]Not enough in this project to score yet.[/dim]")
    else:
        colour = _score_colour(score)
        score_txt = f"{score}/100" if score is not None else "n/a"
        console.print(f"\n[bold]Authoring review[/bold] — score [bold {colour}]{score_txt}[/bold {colour}]")

    # Strengths (evidence-cited; the server sends the phrasing).
    if strengths:
        console.print("\n[bold]Strengths[/bold]")
        for s in strengths[:3]:
            console.print(f"  [green]✓[/green] {s}")

    # The single highest-leverage, author-actionable next step.
    if next_skill:
        console.print("\n[bold]Do this next[/bold]")
        message = next_skill.get("message", "")
        if message:
            console.print(f"  {message}")
        lever = next_skill.get("actionable_via")
        if lever:
            console.print(f"  [dim]via {lever}[/dim]")
        docs = next_skill.get("docs_url")
        if docs:
            console.print(f"  [dim]{docs}[/dim]")
    elif completeness != "thin":
        console.print("\n[green]No higher-leverage improvement to suggest — nice work.[/green]")

    # LLM coaching prose (only present with --coach).
    if coaching:
        console.print("\n[bold]Coaching[/bold]")
        console.print(coaching, markup=False)

    # Full per-check table on --verbose; otherwise a one-line pointer.
    if checks:
        if verbose:
            _print_check_table(checks)
        else:
            console.print(
                f"\n[dim]{len(checks)} checks evaluated. Run with --verbose for the full "
                "table, or --json for the raw report.[/dim]"
            )


_CHECK_STATUS = {
    "pass": "[green]PASS[/green]",
    "warn": "[yellow]WARN[/yellow]",
    "fail": "[red]FAIL[/red]",
    "na": "[dim]n/a[/dim]",
    "info": "[cyan]info[/cyan]",
}


def _print_check_table(checks: list) -> None:
    """Render every rubric check with its status, group, and evidence."""
    table = Table(title="Checks", show_lines=False, expand=False)
    table.add_column("Check", style="bold", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Group", no_wrap=True)
    table.add_column("Evidence")
    for c in checks:
        status = c.get("status", "")
        table.add_row(
            c.get("id", ""),
            _CHECK_STATUS.get(status, status),
            c.get("group", ""),
            c.get("evidence", "") or c.get("why", ""),
        )
    console.print()
    console.print(table)
