"""aethis status — check generation status."""

from __future__ import annotations

from typing import Optional

import typer

from aethis_cli.client import AethisClient
from aethis_cli.config import load_project_config, resolve_api_key
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel


def status(
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p"),
) -> None:
    """Check generation progress for a project."""
    cfg = load_project_config()
    api_key = resolve_api_key(cfg)
    client = AethisClient(api_key, cfg.base_url)

    pid = project_id or cfg.project_id
    if not pid:
        console.print("[red]No project_id. Run 'aethis generate' first or pass --project-id.[/red]")
        raise typer.Exit(code=1)

    try:
        result = client.get_status(pid)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    ps = result.get("project_status", "unknown")
    color = {"ready": "green", "failed": "red", "generating": "yellow"}.get(ps, "white")
    console.print(f"Project: [bold {color}]{ps}[/bold {color}]")

    job = result.get("job")
    if job:
        console.print(f"Job:     {job.get('status')} ({job.get('progress_percent', 0)}%)")
        if job.get("error_message"):
            console.print(f"Error:   [red]{job['error_message']}[/red]")

    bid = result.get("latest_bundle_id")
    if bid:
        console.print(f"Bundle:  {bid}")
