"""aethis publish — activate a generated bundle."""

from __future__ import annotations

from typing import Optional

import typer

from aethis_cli.client import AethisClient
from aethis_cli.config import load_project_config, read_state, resolve_api_key
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel, success


def publish(
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p"),
) -> None:
    """Publish the latest generated bundle (make it active for /decide)."""
    cfg = load_project_config()
    api_key = resolve_api_key(cfg)
    client = AethisClient(api_key, cfg.base_url)

    pid = project_id or cfg.project_id
    if not pid:
        console.print("[red]No project_id. Run 'aethis generate' first or pass --project-id.[/red]")
        raise typer.Exit(code=1)

    try:
        result = client.publish(pid)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    success(f"Published bundle {result.get('bundle_id')}")
