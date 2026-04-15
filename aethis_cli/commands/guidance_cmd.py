"""aethis guidance — list, export, import, and manage guidance hints."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
import yaml

from aethis_cli.client import AethisClient
from aethis_cli.config import load_project_config, resolve_api_key
from aethis_cli.errors import AethisAPIError, ConfigError
from aethis_cli.output import console, error_panel, info, success

guidance_app = typer.Typer(help="Manage guidance hints for rule authoring.")


@guidance_app.command("list")
def list_hints(
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p"),
) -> None:
    """List all guidance hints for a project, with source attribution."""
    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    client = AethisClient(api_key, cfg.base_url)

    pid = project_id or cfg.project_id
    if not pid:
        console.print("[red]No project ID. Run from a project directory or pass --project-id.[/red]")
        raise typer.Exit(code=1)

    try:
        hints = client.list_guidance(pid)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    if not hints:
        console.print("[dim]No guidance hints found.[/dim]")
        return

    for h in hints:
        source_label = {"human": "[green]human[/green]", "agent": "[yellow]agent[/yellow]", "feedback": "[cyan]feedback[/cyan]"}.get(
            h.get("source", "human"), h.get("source", "")
        )
        active = "active" if h.get("active", True) else "[dim]inactive[/dim]"
        version = f"v{h.get('version', 1)}"
        console.print(f"  {source_label} {version} {active}")
        console.print(f"    {h['guidance_text'][:100]}{'...' if len(h['guidance_text']) > 100 else ''}")
        console.print()


@guidance_app.command("export")
def export_hints(
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write to file instead of stdout"),
) -> None:
    """Export active guidance hints as YAML."""
    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    client = AethisClient(api_key, cfg.base_url)

    pid = project_id or cfg.project_id
    if not pid:
        console.print("[red]No project ID.[/red]")
        raise typer.Exit(code=1)

    try:
        data = client.export_guidance(pid)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    yaml_output = yaml.dump(data, default_flow_style=False, sort_keys=False)

    if output:
        output.write_text(yaml_output)
        success(f"Exported {len(data.get('hints', []))} hint(s) to {output}")
    else:
        console.print(yaml_output)


@guidance_app.command("deactivate")
def deactivate_hint(
    hint_id: str = typer.Argument(..., help="UUID of the hint to deactivate"),
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p"),
) -> None:
    """Soft-deactivate a hint (preserved in database, excluded from future runs)."""
    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    client = AethisClient(api_key, cfg.base_url)

    pid = project_id or cfg.project_id
    if not pid:
        console.print("[red]No project ID.[/red]")
        raise typer.Exit(code=1)

    try:
        client.deactivate_guidance(pid, hint_id)
        success(f"Hint {hint_id} deactivated")
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)


@guidance_app.command("import")
def import_hints(
    file: Path = typer.Argument(..., help="YAML file with hints to import"),
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p"),
) -> None:
    """Import guidance hints from a YAML file (creates new hints, doesn't overwrite)."""
    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    if not file.exists():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(code=1)

    raw = yaml.safe_load(file.read_text()) or {}
    hints = raw.get("hints", [])

    if not hints:
        console.print("[dim]No hints found in file.[/dim]")
        return

    client = AethisClient(api_key, cfg.base_url)

    pid = project_id or cfg.project_id
    if not pid:
        console.print("[red]No project ID.[/red]")
        raise typer.Exit(code=1)

    try:
        count = 0
        skipped = 0
        for hint in hints:
            if isinstance(hint, str):
                text = hint
                source = "human"
                process_type = "rule_generation"
            else:
                text = hint.get("text", "")
                source = hint.get("source", "human")
                process_type = hint.get("process_type", "rule_generation")
            if text:
                result = client.add_guidance(pid, text, source=source, process_type=process_type)
                if result.get("skipped"):
                    skipped += 1
                else:
                    count += 1
        parts = [f"Imported {count} hint(s)"]
        if skipped:
            parts.append(f"{skipped} skipped (already exist)")
        success(", ".join(parts))
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)
