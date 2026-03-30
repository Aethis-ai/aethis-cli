"""aethis projects — list, show, and archive authoring projects."""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from aethis_cli.client import AethisClient
from aethis_cli.config import load_project_config, resolve_api_key
from aethis_cli.errors import AethisAPIError, ConfigError
from aethis_cli.output import console, error_panel, success

projects_app = typer.Typer(
    name="projects",
    help="List, show, and archive authoring projects.",
    no_args_is_help=True,
)


def _get_client() -> AethisClient:
    cfg = load_project_config()
    api_key = resolve_api_key(cfg)
    return AethisClient(api_key, cfg.base_url)


@projects_app.command(name="list")
def list_projects(
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived projects"),
) -> None:
    """List all projects."""
    try:
        client = _get_client()
    except ConfigError:
        # No aethis.yaml needed for listing — use defaults
        from aethis_cli.config import DEFAULT_BASE_URL, resolve_api_key as _resolve
        import os
        base_url = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)
        from aethis_cli.config import ProjectConfig
        cfg = ProjectConfig(project="", base_url=base_url)
        api_key = _resolve(cfg)
        client = AethisClient(api_key, base_url)

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


@projects_app.command(name="show")
def show_project(
    project_id: str = typer.Argument(..., help="Project ID (proj_...)"),
) -> None:
    """Show project details."""
    try:
        client = _get_client()
    except ConfigError:
        from aethis_cli.config import DEFAULT_BASE_URL, resolve_api_key as _resolve
        import os
        base_url = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)
        from aethis_cli.config import ProjectConfig
        cfg = ProjectConfig(project="", base_url=base_url)
        api_key = _resolve(cfg)
        client = AethisClient(api_key, base_url)

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

    try:
        client = _get_client()
    except ConfigError:
        from aethis_cli.config import DEFAULT_BASE_URL, resolve_api_key as _resolve
        import os
        base_url = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)
        from aethis_cli.config import ProjectConfig
        cfg = ProjectConfig(project="", base_url=base_url)
        api_key = _resolve(cfg)
        client = AethisClient(api_key, base_url)

    try:
        result = client.archive_project(project_id)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    success(f"Project {project_id} archived (was: {result.get('previous_status', 'unknown')})")
