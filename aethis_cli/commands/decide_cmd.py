"""aethis decide — quick eligibility check."""

from __future__ import annotations

import json
from typing import Optional

import typer

from aethis_cli.client import AethisClient
from aethis_cli.config import load_project_config, read_state, resolve_api_key
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel


def decide(
    input_json: str = typer.Option(..., "--input", "-i", help="JSON object of field values"),
    bundle_id: Optional[str] = typer.Option(None, "--bundle-id", "-b", help="Bundle ID (default: from .aethis/state.json)"),
) -> None:
    """Evaluate eligibility against a published bundle."""
    cfg = load_project_config()
    api_key = resolve_api_key(cfg)
    client = AethisClient(api_key, cfg.base_url)

    if not bundle_id:
        state = read_state(cfg.config_path)
        bundle_id = state.get("bundle_id")
        if not bundle_id:
            console.print("[red]No bundle_id found. Run 'aethis generate' and 'aethis publish' first, or pass --bundle-id.[/red]")
            raise typer.Exit(code=1)

    try:
        field_values = json.loads(input_json)
    except json.JSONDecodeError as e:
        console.print(f"[red]Invalid JSON: {e}[/red]")
        raise typer.Exit(code=1)

    try:
        result = client.decide(bundle_id, field_values)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    decision = result["decision"]
    color = {"eligible": "green", "not_eligible": "red"}.get(decision, "yellow")
    console.print(f"\nDecision: [bold {color}]{decision}[/bold {color}]")
    console.print(f"Bundle:   {result.get('bundle_id')}")
    console.print(f"Fields:   {result.get('fields_provided')}/{result.get('fields_evaluated')} provided")
    if result.get("missing_fields"):
        console.print(f"Missing:  {', '.join(result['missing_fields'])}")
