"""aethis test — run golden test cases against the generated bundle."""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from aethis_cli.config import load_project_config, make_authed_client, resolve_api_key, write_state
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel


def test(
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p"),
) -> None:
    """Run golden test cases against the latest generated bundle."""
    cfg = load_project_config()
    api_key = resolve_api_key(cfg)
    client = make_authed_client(api_key, cfg.base_url)

    pid = project_id or cfg.project_id
    if not pid:
        console.print("[red]No project_id. Run 'aethis generate' first or pass --project-id.[/red]")
        raise typer.Exit(code=1)

    try:
        result = client.run_tests(pid)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    # Zero test cases → warn and fail. Silent 0/0 pass looks like success and
    # masks the fact that no assertions ran.
    if result.get("total", 0) == 0:
        console.print(
            "[yellow]No test cases in this project.[/yellow]\n"
            "[dim]Add at least one scenario to tests/scenarios.yaml before running 'aethis test'.[/dim]"
        )
        raise typer.Exit(code=1)

    # Save test results
    write_state(cfg.config_path, {"last_test_passed": result["passed"], "last_test_total": result["total"]})

    total = result["total"]
    passed = result["passed"]
    failed = result["failed"]
    errors = result.get("errors", 0)

    table = Table(title="Test Results")
    table.add_column("Name")
    table.add_column("Expected")
    table.add_column("Actual")
    table.add_column("Result")

    for r in result.get("results", []):
        status_str = "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]"
        if r.get("error"):
            status_str = "[yellow]ERROR[/yellow]"
        table.add_row(
            r["name"],
            r.get("expected", ""),
            r.get("actual", ""),
            status_str,
        )

    console.print(table)

    color = "green" if failed == 0 and errors == 0 else "red"
    console.print(f"\n[bold {color}]{passed}/{total} passed[/bold {color}]", end="")
    if failed:
        console.print(f", {failed} failed", end="")
    if errors:
        console.print(f", {errors} errors", end="")
    console.print()

    if failed > 0 or errors > 0:
        raise typer.Exit(code=1)
