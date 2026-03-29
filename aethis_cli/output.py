"""Output helpers — clean prefixed text, no panels/boxes."""

from __future__ import annotations

import sys

from rich.console import Console
from rich.table import Table

from aethis_cli.errors import AethisAPIError

console = Console()


def error_panel(e: AethisAPIError) -> None:
    """Render an API error as a single line."""
    console.print(f"[red]Error: {e.detail} (HTTP {e.status_code})[/red]", highlight=False)


def success(msg: str) -> None:
    console.print(f"[green]✓[/green] {msg}")


def info(msg: str) -> None:
    console.print(f"[dim]→[/dim] {msg}")


def warn(msg: str) -> None:
    console.print(f"[yellow]![/yellow] {msg}")
