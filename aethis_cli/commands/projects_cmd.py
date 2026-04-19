"""aethis projects — list, show, and archive authoring projects."""

from __future__ import annotations

import typer
from rich.table import Table

from aethis_cli.config import load_client_or_fallback
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel, success

projects_app = typer.Typer(
    name="projects",
    help="List, show, and archive authoring projects.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


@projects_app.command(name="list")
def list_projects(
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived projects"),
) -> None:
    """List all projects.

    Examples:

        aethis projects list
        aethis projects list --include-archived
        aethis --base-url http://localhost:8080 projects list
    """
    _cfg, client = load_client_or_fallback()

    try:
        projects = client.list_projects(include_archived=include_archived)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    if not projects:
        console.print("[dim]No projects found.[/dim]")
        return

    table = Table(title="Projects")
    table.add_column("Project ID", style="cyan")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Bundle")
    table.add_column("Created")

    for p in projects:
        status = p.get("status", "")
        style = "dim" if status == "archived" else None
        table.add_row(
            p["project_id"],
            p["name"],
            status,
            p.get("latest_bundle_id") or "",
            p.get("created_at", "")[:10] if p.get("created_at") else "",
            style=style,
        )

    console.print(table)
    console.print(
        "[dim]Tip: copy a Bundle value and run "
        "`aethis explain -b <bundle>` or `aethis decide -b <bundle> -i '{...}'`.[/dim]"
    )


@projects_app.command(name="show")
def show_project(
    project_id: str = typer.Argument(..., help="Project ID (proj_...)"),
) -> None:
    """Show project details."""
    _cfg, client = load_client_or_fallback()

    try:
        p = client.get_project(project_id)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    console.print(f"[bold]{p['name']}[/bold] ({p['project_id']})")
    console.print(f"  Section:  {p.get('section_id', '')}")
    console.print(f"  Status:   {p['status']}")
    console.print(f"  Bundle:   {p.get('latest_bundle_id') or 'none'}")
    console.print(f"  Tests:    {p.get('test_case_count', 0)}")
    console.print(f"  Created:  {p.get('created_at', '')[:19]}")
    console.print(f"  Updated:  {p.get('updated_at', '')[:19]}")


@projects_app.command(name="archive")
def archive_project(
    project_id: str = typer.Argument(..., help="Project ID (proj_...)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Archive a project (soft-delete, preserves all data)."""
    if not yes:
        confirmed = typer.confirm(f"Archive project {project_id}? This cannot be undone")
        if not confirmed:
            raise typer.Abort()

    _cfg, client = load_client_or_fallback()

    try:
        result = client.archive_project(project_id)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    success(f"Project {project_id} archived (was: {result.get('previous_status', 'unknown')})")
