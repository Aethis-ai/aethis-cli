"""aethis domain guidance — manage domain-level guidance hints."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
import yaml

from aethis_cli.client import AethisClient
from aethis_cli.config import load_project_config, resolve_api_key
from aethis_cli.errors import AethisAPIError, ConfigError
from aethis_cli.output import console, error_panel, info, success

domain_guidance_app = typer.Typer(help="Manage domain-level guidance hints.")

_PROCESS_TYPES = ["rule_generation", "field_extraction"]


def _get_client() -> tuple[AethisClient, str]:
    """Load config and return (client, base_url). Raises ConfigError on failure."""
    cfg = load_project_config()
    api_key = resolve_api_key(cfg)
    return AethisClient(api_key, cfg.base_url), cfg.base_url


@domain_guidance_app.command("add")
def add_domain_hint(
    domain: str = typer.Argument(..., help="Domain identifier (e.g. uk_citizenship)"),
    text: str = typer.Argument(..., help="Guidance hint text"),
    process_type: str = typer.Option(
        "rule_generation",
        "--process-type",
        help="Process type: rule_generation or field_extraction",
    ),
    notes: Optional[str] = typer.Option(None, "--notes", help="Optional notes for this hint"),
) -> None:
    """Add a guidance hint to a domain."""
    if process_type not in _PROCESS_TYPES:
        console.print(f"[red]Invalid process_type '{process_type}'. Choose: {', '.join(_PROCESS_TYPES)}[/red]")
        raise typer.Exit(code=1)

    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    client = AethisClient(api_key, cfg.base_url)

    try:
        result = client.add_domain_guidance(domain, text, process_type=process_type, notes=notes)
        hint_id = result.get("hint_id", "")
        success(f"Added hint {hint_id} to domain '{domain}' [{process_type}]")
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)


@domain_guidance_app.command("list")
def list_domain_hints(
    domain: str = typer.Argument(..., help="Domain identifier (e.g. uk_citizenship)"),
) -> None:
    """List all guidance hints for a domain."""
    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    client = AethisClient(api_key, cfg.base_url)

    try:
        hints = client.list_domain_guidance(domain)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    if not hints:
        console.print("[dim]No guidance hints found.[/dim]")
        return

    for h in hints:
        pt = h.get("process_type", "rule_generation")
        pt_label = (
            "[blue]field_extraction[/blue]" if pt == "field_extraction" else "[green]rule_generation[/green]"
        )
        active = "active" if h.get("active", True) else "[dim]inactive[/dim]"
        console.print(f"  {pt_label} {active}")
        console.print(f"    {h['guidance_text'][:120]}{'...' if len(h['guidance_text']) > 120 else ''}")
        if h.get("notes"):
            console.print(f"    [dim]notes: {h['notes']}[/dim]")
        console.print()


@domain_guidance_app.command("import")
def import_domain_hints(
    domain: str = typer.Argument(..., help="Domain identifier (e.g. uk_citizenship)"),
    file: Path = typer.Argument(..., help="YAML file with hints to import"),
) -> None:
    """Import guidance hints from a YAML file into a domain.

    The top-level 'domain' key in the YAML file is ignored; use the CLI argument instead.
    """
    if not file.exists():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(code=1)

    try:
        raw = yaml.safe_load(file.read_text()) or {}
    except yaml.YAMLError as e:
        console.print(f"[red]Invalid YAML in {file}: {e}[/red]")
        raise typer.Exit(code=1)

    hints = raw.get("hints", [])
    if not hints:
        console.print("[dim]No hints found in file.[/dim]")
        return

    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    client = AethisClient(api_key, cfg.base_url)

    try:
        count = 0
        skipped = 0
        for hint in hints:
            if isinstance(hint, str):
                text = hint
                process_type = "rule_generation"
                notes = None
            else:
                text = hint.get("text", "")
                process_type = hint.get("process_type", "rule_generation")
                notes = hint.get("notes")

            if not text:
                continue

            if process_type not in _PROCESS_TYPES:
                console.print(
                    f"[yellow]Skipping hint with invalid process_type '{process_type}': {text[:60]}[/yellow]"
                )
                continue

            result = client.add_domain_guidance(domain, text, process_type=process_type, notes=notes)
            if result.get("skipped"):
                skipped += 1
            else:
                count += 1

        parts = [f"Imported {count} hint(s) into domain '{domain}'"]
        if skipped:
            parts.append(f"{skipped} skipped (already exist)")
        success(", ".join(parts))
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)


@domain_guidance_app.command("export")
def export_domain_hints(
    domain: str = typer.Argument(..., help="Domain identifier (e.g. uk_citizenship)"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write to file instead of stdout"),
) -> None:
    """Export domain guidance hints as a YAML file."""
    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    client = AethisClient(api_key, cfg.base_url)

    try:
        hints = client.list_domain_guidance(domain)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    serialisable = []
    for h in hints:
        entry: dict = {"text": h.get("guidance_text", "")}
        pt = h.get("process_type")
        if pt:
            entry["process_type"] = pt
        notes = h.get("notes")
        if notes:
            entry["notes"] = notes
        serialisable.append(entry)

    data = {"domain": domain, "hints": serialisable}
    yaml_output = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    if output:
        output.write_text(yaml_output)
        success(f"Exported {len(serialisable)} hint(s) to {output}")
    else:
        console.print(yaml_output)
