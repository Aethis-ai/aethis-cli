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
    if isinstance(detail, list):
        # FastAPI request-validation envelope: a list of
        # {type, loc, msg, input} entries. The API forbids unknown top-level
        # request keys, so "extra_forbidden" is a shape a caller will hit --
        # naming the offending member beats printing a raw list repr.
        parts = []
        for item in detail:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            loc = item.get("loc") or []
            where = ".".join(str(p) for p in loc if p != "body") if isinstance(loc, list) else str(loc)
            msg = item.get("msg") or item.get("type") or "invalid"
            kind = item.get("type")
            if kind == "extra_forbidden":
                msg = "not a member of this request; the API does not accept it"
            parts.append(f"{where or 'request'}: {msg}" if where or msg else str(item))
        return "Request rejected — " + "; ".join(parts) if parts else "Request rejected"
    if isinstance(detail, dict):
        reason = detail.get("reason_code", "unknown")
        action = detail.get("action", "unknown")
        missing = detail.get("missing_permissions", [])
        # Coerce each item to str — a non-string element (a server quirk) must
        # not turn a readable error into a raw TypeError traceback.
        missing_str = ", ".join(str(m) for m in missing) if isinstance(missing, list) else str(missing)
        message = detail.get("message") or detail.get("error") or "Request denied"
        return f"{message} (reason={reason}, action={action}, missing={missing_str})"
    return str(detail)


def render_failure_list(detail: object) -> None:
    """Print the per-item ``failures`` list a fail-closed endpoint returns.

    Publish-time citation resolution rejects the WHOLE publish and reports
    one entry per failing citation key (``{source_id, reason_code, message}``
    — see the engine's ``source_reference_resolution_failed`` envelope).
    Collapsing that to the envelope's summary line tells an author with three
    citations that "a citation failed" and nothing about which one or why,
    which is the difference between a fixable error and a guessing game.
    """
    failures = detail.get("failures") if isinstance(detail, dict) else None
    if not isinstance(failures, list) or not failures:
        return
    console.print(f"\n[red]{len(failures)} citation(s) could not be resolved:[/red]", highlight=False)
    for failure in failures:
        if not isinstance(failure, dict):
            console.print(f"  - {failure}", markup=False, highlight=False)
            continue
        key = failure.get("source_id") or "(unknown key)"
        reason = failure.get("reason_code") or "unknown"
        message = failure.get("message") or ""
        # markup=False throughout: a server message may contain [brackets]
        # (a locator, a quote) that Rich would otherwise eat.
        console.print(f"  {key}  [{reason}]", markup=False, highlight=False)
        if message:
            console.print(f"      {message}", style="dim", markup=False, highlight=False)


def render_api_error(status_code: int, detail: object) -> None:
    """Print an API error envelope readably: one red line, any per-item
    ``failures`` the server itemised, plus a dim follow-up when the server
    supplied a ``hint`` so the caller sees what to do next."""
    console.print(
        f"[red]Error: {format_error_detail(detail)} (HTTP {status_code})[/red]",
        highlight=False,
    )
    render_failure_list(detail)
    hint = detail.get("hint") if isinstance(detail, dict) else None
    if hint:
        # markup=False so server hint text containing [brackets] isn't parsed
        # as Rich markup and silently dropped.
        console.print(hint, style="dim", markup=False)
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
