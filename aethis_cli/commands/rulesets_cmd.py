"""aethis rulesets — list and archive rulesets."""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from aethis_cli.client import make_anonymous_client
from aethis_cli.commands._id_utils import require_ruleset_id
from aethis_cli.config import load_client_or_fallback, resolve_base_url_with_source
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel, success, warn

rulesets_app = typer.Typer(
    name="rulesets",
    help="List and archive rulesets.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _print_public_table(rulesets: list[dict]) -> None:
    table = Table(title="Public showcase rulesets")
    table.add_column("Slug", style="cyan")
    table.add_column("Ruleset ID", style="dim")
    table.add_column("Description")
    table.add_column("Fields", justify="right")
    table.add_column("Rules", justify="right")

    for r in rulesets:
        table.add_row(
            r.get("slug") or "[dim]—[/dim]",
            r.get("ruleset_id", ""),
            r.get("description", "") or "[dim]—[/dim]",
            str(r.get("field_count", 0)),
            str(r.get("rule_count", 0)),
        )

    console.print(table)


def _list_public(limit: int, offset: int) -> None:
    """Hit the anonymous catalogue endpoint and render the result."""
    base_url, _ = resolve_base_url_with_source()
    with make_anonymous_client(base_url) as client:
        try:
            rulesets = client.list_public_rulesets(limit=limit, offset=offset)
        except AethisAPIError as e:
            error_panel(e)
            raise typer.Exit(code=1)

    if not rulesets:
        console.print("[dim]No public rulesets published yet.[/dim]")
        return

    _print_public_table(rulesets)
    console.print(
        "\n[dim]Try: aethis fields -b <slug>  ·  aethis explain -b <slug>  ·  aethis decide -b <slug> -i '{...}'[/dim]"
    )


@rulesets_app.command(name="list")
def list_rulesets(
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p", help="Project ID"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status (comma-separated)"),
    public: bool = typer.Option(
        False,
        "--public",
        help="List the cross-tenant public showcase catalogue (no auth required).",
    ),
    limit: int = typer.Option(20, "--limit", min=1, max=50, help="Max rulesets to return (public mode)."),
    offset: int = typer.Option(0, "--offset", min=0, help="Pagination offset (public mode)."),
) -> None:
    """List rulesets for a project, or the public showcase catalogue.

    Examples:

        aethis rulesets list                                  # auto: showcase if no project context
        aethis rulesets list --public                         # explicit: anonymous catalogue
        aethis rulesets list -p proj_i1HyinBtFJniayUC         # tenant-scoped
        aethis rulesets list -p proj_i1HyinBtFJniayUC -s active,archived
    """
    if public:
        _list_public(limit=limit, offset=offset)
        return

    # Resolve the project id without forcing a login first — if there's no
    # project context we want to fall through to the public catalogue rather
    # than ask the user to authenticate just to discover what's available.
    from aethis_cli.config import load_project_config, read_state
    from aethis_cli.errors import ConfigError

    pid: Optional[str] = project_id
    if not pid:
        try:
            cfg = load_project_config()
            pid = read_state(cfg.config_path).get("project_id")
        except ConfigError:
            pid = None

    if not pid:
        console.print(
            "[dim]No project context — showing public showcase rulesets. Pass --project-id <id> to list your own.[/dim]"
        )
        _list_public(limit=limit, offset=offset)
        return

    cfg, client = load_client_or_fallback()

    try:
        rulesets = client.list_rulesets(pid, status=status)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    if not rulesets:
        console.print("[dim]No rulesets found.[/dim]")
        return

    table = Table(title=f"Rulesets — {pid}")
    table.add_column("Ruleset ID", style="cyan")
    table.add_column("Status")
    table.add_column("Fields", justify="right")
    table.add_column("Rules", justify="right")
    table.add_column("Version")
    table.add_column("Created")

    for b in rulesets:
        s = b.get("status", "")
        style = "dim" if s == "archived" else None
        table.add_row(
            b["ruleset_id"],
            s,
            str(b.get("total_fields", 0)),
            str(b.get("total_rules", 0)),
            b.get("version", ""),
            b.get("created_at", "")[:10] if b.get("created_at") else "",
            style=style,
        )

    console.print(table)


@rulesets_app.command(name="archive")
def archive_ruleset(
    ruleset_id: str = typer.Argument(
        ...,
        help="Ruleset ID (e.g. example_ruleset:20260408-abc1234). Not a Project ID.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Archive a ruleset (soft-delete, preserves all data)."""
    require_ruleset_id(ruleset_id)

    if not yes:
        confirmed = typer.confirm(f"Archive ruleset {ruleset_id}? This cannot be undone")
        if not confirmed:
            raise typer.Abort()

    _cfg, client = load_client_or_fallback()

    try:
        result = client.archive_ruleset(ruleset_id)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    success(f"Ruleset {ruleset_id} archived (was: {result.get('previous_status', 'unknown')})")

    if result.get("warning"):
        warn(result["warning"])
