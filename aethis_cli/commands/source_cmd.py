"""aethis source — show generated Python DSL source code (internal only)."""

from __future__ import annotations

from typing import Optional

import typer
from rich.syntax import Syntax

from aethis_cli.client import AethisClient
from aethis_cli.config import load_project_config, read_state, resolve_api_key
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel


def source(
    bundle_id: Optional[str] = typer.Option(None, "--bundle-id", "-b"),
) -> None:
    """Show the generated Python DSL source code for a bundle.

    Requires an API key with the bundles:source scope (internal only).
    """
    cfg = load_project_config()
    api_key = resolve_api_key(cfg)
    client = AethisClient(api_key, cfg.base_url)

    if not bundle_id:
        state = read_state(cfg.config_path)
        bundle_id = state.get("bundle_id")
        if not bundle_id:
            console.print("[red]No bundle_id. Run 'aethis generate' first or pass --bundle-id.[/red]")
            raise typer.Exit(code=1)

    try:
        result = client.get_source(bundle_id)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    python_source = result.get("python_source", "")
    if not python_source:
        console.print(f"[yellow]No source code found for bundle {bundle_id}.[/yellow]")
        raise typer.Exit(code=1)

    console.print(f"[bold]Bundle:[/bold] {bundle_id}  [bold]Version:[/bold] {result.get('version', '?')}")
    console.print()
    console.print(Syntax(python_source, "python", theme="monokai", line_numbers=True))
