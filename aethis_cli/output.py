"""Output helpers — clean prefixed text, no panels/boxes."""

from __future__ import annotations

from rich.console import Console

from aethis_cli.errors import AethisAPIError

console = Console()


def format_error_detail(detail: object) -> str:
    """Collapse an API error ``detail`` into one readable line.

    A structured envelope (``{message, reason_code, action,
    missing_permissions}``) becomes a human line naming the missing scope
    rather than a raw ``dict`` repr; a plain-string detail passes through.
    """
    if isinstance(detail, dict):
        reason = detail.get("reason_code", "unknown")
        action = detail.get("action", "unknown")
        missing = detail.get("missing_permissions", [])
        missing_str = ", ".join(missing) if isinstance(missing, list) else str(missing)
        message = detail.get("message") or detail.get("error") or "Request denied"
        return f"{message} (reason={reason}, action={action}, missing={missing_str})"
    return str(detail)


def render_api_error(status_code: int, detail: object) -> None:
    """Print an API error envelope readably: one red line plus, when the
    server supplied a ``hint``, a dim follow-up line so the caller sees what
    to do next (e.g. how to request the missing scope)."""
    console.print(
        f"[red]Error: {format_error_detail(detail)} (HTTP {status_code})[/red]",
        highlight=False,
    )
    hint = detail.get("hint") if isinstance(detail, dict) else None
    if hint:
        console.print(f"[dim]{hint}[/dim]")
    if status_code == 401:
        console.print("[dim]Run 'aethis login' to re-authenticate.[/dim]")


def error_panel(e: AethisAPIError) -> None:
    """Render an API error as a readable line (+ hint when present)."""
    render_api_error(e.status_code, e.detail)


def success(msg: str) -> None:
    console.print(f"[green]✓[/green] {msg}")


def info(msg: str) -> None:
    console.print(f"[dim]→[/dim] {msg}")


def warn(msg: str) -> None:
    console.print(f"[yellow]![/yellow] {msg}")
