"""Rich output helpers — shared console, tables, panels."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from aethis_cli.errors import AethisAPIError

console = Console()


def error_panel(e: AethisAPIError) -> None:
    """Render an API error as a red panel."""
    console.print(Panel(
        f"[bold]{e.detail}[/bold]",
        title=f"HTTP {e.status_code}",
        border_style="red",
    ))


def success(msg: str) -> None:
    console.print(f"[bold green]{msg}[/bold green]")


def info(msg: str) -> None:
    console.print(f"[cyan]{msg}[/cyan]")
