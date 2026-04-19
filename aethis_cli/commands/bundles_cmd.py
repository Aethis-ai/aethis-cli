"""aethis bundles — list and archive rule bundles."""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from aethis_cli.client import AethisClient
from aethis_cli.config import load_project_config, resolve_api_key
from aethis_cli.errors import AethisAPIError, ConfigError
from aethis_cli.output import console, error_panel, success, warn

bundles_app = typer.Typer(
    name="bundles",
    help="List and archive rule bundles.",
    no_args_is_help=True,
)


@bundles_app.command(name="list")
def list_bundles(
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p", help="Project ID"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status (comma-separated)"),
) -> None:
    """List bundles for a project."""
    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
        client = AethisClient(api_key, cfg.base_url)
        pid = project_id or cfg.project_id
    except ConfigError:
        if not project_id:
            console.print("[red]No project context. Pass --project-id or run from a project directory.[/red]")
            raise typer.Exit(code=1)
        import os
        from aethis_cli.config import DEFAULT_BASE_URL, ProjectConfig

        base_url = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)
        cfg = ProjectConfig(project="", base_url=base_url)
        api_key = resolve_api_key(cfg)
        client = AethisClient(api_key, base_url)
        pid = project_id

    if not pid:
        console.print("[red]No project_id. Pass --project-id or run 'aethis generate' first.[/red]")
        raise typer.Exit(code=1)

    try:
        bundles = client.list_bundles(pid, status=status)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    if not bundles:
        console.print("[dim]No bundles found.[/dim]")
        return

    table = Table(title=f"Bundles — {pid}")
    table.add_column("Bundle ID", style="cyan")
    table.add_column("Status")
    table.add_column("Fields", justify="right")
    table.add_column("Rules", justify="right")
    table.add_column("Version")
    table.add_column("Created")

    for b in bundles:
        s = b.get("status", "")
        style = "dim" if s == "archived" else None
        table.add_row(
            b["bundle_id"],
            s,
            str(b.get("total_fields", 0)),
            str(b.get("total_rules", 0)),
            b.get("version", ""),
            b.get("created_at", "")[:10] if b.get("created_at") else "",
            style=style,
        )

    console.print(table)


@bundles_app.command(name="archive")
def archive_bundle(
    bundle_id: str = typer.Argument(..., help="Bundle ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Archive a bundle (soft-delete, preserves all data)."""
    if not yes:
        confirmed = typer.confirm(f"Archive bundle {bundle_id}? This cannot be undone")
        if not confirmed:
            raise typer.Abort()

    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
        client = AethisClient(api_key, cfg.base_url)
    except ConfigError:
        import os
        from aethis_cli.config import DEFAULT_BASE_URL, ProjectConfig

        base_url = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)
        cfg = ProjectConfig(project="", base_url=base_url)
        api_key = resolve_api_key(cfg)
        client = AethisClient(api_key, base_url)

    try:
        result = client.archive_bundle(bundle_id)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    success(f"Bundle {bundle_id} archived (was: {result.get('previous_status', 'unknown')})")

    if result.get("warning"):
        warn(result["warning"])
