"""aethis rulesets — list, create, show, promote, archive rulesets.

In the converged 2-term authoring model (`docs/RULEBOOK_AUTHORING_MODEL.md`
in the workspace), a Ruleset is the named, versioned part of a Rulebook.
The lifecycle commands added in Phase B.1b — ``create``, ``show``,
``promote-to-live`` — scope to a rulebook explicitly (positional first
argument). The legacy ``-p <project_id>`` and ``--public`` modes of
``list`` are preserved while the project-scoped authoring pipeline is
retired in a future phase.
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from aethis_cli.client import make_anonymous_client
from aethis_cli.commands._id_utils import require_ruleset_id
from aethis_cli.config import load_client_or_fallback, resolve_base_url_with_source
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel, success, warn
from aethis_cli.render import emit, is_json_requested

rulesets_app = typer.Typer(
    name="rulesets",
    help="List, create, show, promote, archive rulesets.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _build_public_table(rulesets: list[dict]) -> Table:
    table = Table(title="Public showcase rulesets")
    table.add_column("Slug", style="cyan")
    table.add_column("Ruleset ID", style="dim")
    table.add_column("Name")
    table.add_column("Description")
    table.add_column("Fields", justify="right")
    table.add_column("Rules", justify="right")

    for r in rulesets:
        table.add_row(
            r.get("slug") or "[dim]—[/dim]",
            r.get("ruleset_id", ""),
            r.get("name") or "[dim]—[/dim]",
            r.get("description", "") or "[dim]—[/dim]",
            str(r.get("field_count", 0)),
            str(r.get("rule_count", 0)),
        )
    return table


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
        if is_json_requested():
            emit([])
        else:
            console.print("[dim]No public rulesets published yet.[/dim]")
        return

    emit(rulesets, table=lambda: _build_public_table(rulesets))
    if not is_json_requested():
        console.print(
            "\n[dim]Try: aethis fields -b <slug>  ·  aethis explain -b <slug>  ·  aethis decide -b <slug> -i '{...}'[/dim]"
        )


@rulesets_app.command(name="list")
def list_rulesets(
    rulebook: Optional[str] = typer.Argument(
        None,
        help=("Rulebook ID or slug to list rulesets for. When given, takes precedence over --project-id and --public."),
    ),
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p", help="Legacy: list rulesets for a project."),
    status: Optional[str] = typer.Option(
        None, "--status", "-s", help="Filter by status (comma-separated; project mode only)."
    ),
    public: bool = typer.Option(
        False,
        "--public",
        help="List the cross-tenant public showcase catalogue (no auth required).",
    ),
    limit: int = typer.Option(20, "--limit", min=1, max=50, help="Max rulesets to return (public mode)."),
    offset: int = typer.Option(0, "--offset", min=0, help="Pagination offset (public mode)."),
) -> None:
    """List rulesets — by rulebook (new), by project (legacy), or the public showcase.

    Examples:

        aethis rulesets list aethis/uk-fsm                    # rulebook-scoped (new)
        aethis rulesets list rb_abc123                        # by rulebook_id
        aethis rulesets list --public                         # explicit: anonymous catalogue
        aethis rulesets list                                  # auto: showcase if no project context
        aethis rulesets list -p proj_i1HyinBtFJniayUC         # tenant-scoped (legacy)
    """
    if rulebook:
        _list_rulebook_rulesets(rulebook)
        return

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
        if not is_json_requested():
            console.print(
                "[dim]No project context — showing public showcase rulesets. "
                "Pass <rulebook> or --project-id <id> to list your own.[/dim]"
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
        if is_json_requested():
            emit([])
        else:
            console.print("[dim]No rulesets found.[/dim]")
        return

    def _build_project_table() -> Table:
        table = Table(title=f"Rulesets — {pid}")
        table.add_column("Ruleset ID", style="cyan")
        table.add_column("Name")
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
                b.get("name") or "[dim]—[/dim]",
                s,
                str(b.get("total_fields", 0)),
                str(b.get("total_rules", 0)),
                b.get("version", ""),
                b.get("created_at", "")[:10] if b.get("created_at") else "",
                style=style,
            )
        return table

    emit(rulesets, table=_build_project_table)


def _list_rulebook_rulesets(rulebook: str) -> None:
    """List rulesets in a rulebook using the Phase A.8 endpoint."""
    _cfg, client = load_client_or_fallback()
    try:
        resp = client.list_rulesets_in_rulebook(rulebook)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    rulesets = resp.get("rulesets", [])
    if not rulesets:
        if is_json_requested():
            emit([])
        else:
            console.print(
                f"[dim]No rulesets in rulebook {rulebook!r} yet. "
                "Create one with `aethis rulesets create <rulebook> <name>`.[/dim]"
            )
        return

    def _build_rulebook_table() -> Table:
        table = Table(title=f"Rulesets in {rulebook}")
        table.add_column("Ruleset name", style="cyan")
        table.add_column("Display name")
        table.add_column("Versions", justify="right")
        table.add_column("Live version")
        table.add_column("States seen")
        for r in rulesets:
            table.add_row(
                r.get("ruleset_name", ""),
                r.get("display_name") or "[dim]—[/dim]",
                str(r.get("version_count", 0)),
                r.get("live_version") or "[dim]—[/dim]",
                ", ".join(r.get("states", [])) or "[dim]—[/dim]",
            )
        return table

    emit(rulesets, table=_build_rulebook_table)


# ============================================================================
# Phase B.1b — ruleset lifecycle commands scoped to a rulebook.
# ============================================================================


@rulesets_app.command(name="create")
def create_ruleset(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug to create the ruleset in."),
    ruleset_name: str = typer.Argument(..., help="Identifier-style name (lower_snake_case)."),
    display_name: Optional[str] = typer.Option(
        None,
        "--name",
        "-n",
        help=(
            "Human-readable display name. Defaults to a titlecased version "
            "of ruleset_name (`child_eligibility` → `Child Eligibility`)."
        ),
    ),
) -> None:
    """Create a new draft Ruleset within a Rulebook.

    The ruleset starts in `draft` state with no compiled DSL. Authoring
    (sources, guidance, generate, test) flows through the project-scoped
    endpoints today; a future phase wires those to the new ruleset path.

    Examples::

        aethis rulesets create aethis/uk-fsm child_eligibility
        aethis rulesets create rb_abc household_criteria \\
            -n "Household qualifying criteria"
    """
    if display_name is None:
        display_name = ruleset_name.replace("_", " ").title()

    _cfg, client = load_client_or_fallback()
    try:
        rs = client.create_ruleset_in_rulebook(rulebook, ruleset_name=ruleset_name, name=display_name)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    success(
        f"Created draft ruleset {rs['ruleset_name']!r} (bundle_id: {rs['bundle_id']}) in rulebook {rs['rulebook_id']}"
    )
    console.print_json(data=rs)


@rulesets_app.command(name="show")
def show_ruleset(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
    ruleset_name: str = typer.Argument(..., help="Ruleset name within the rulebook."),
) -> None:
    """Show the version history for one ruleset_name in a rulebook."""
    _cfg, client = load_client_or_fallback()
    try:
        resp = client.show_ruleset_in_rulebook(rulebook, ruleset_name)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    versions = resp.get("versions", [])
    live_version = resp.get("live_version")
    display_name = resp.get("display_name")

    if is_json_requested():
        emit(resp)
        return

    console.print(
        f"[bold]{ruleset_name}[/bold]  in  [cyan]{rulebook}[/cyan]"
        + (f"  · [dim]{display_name}[/dim]" if display_name else "")
    )
    if live_version:
        console.print(f"  live version: [green]{live_version}[/green]")
    else:
        console.print("  [dim]no live version (never promoted)[/dim]")

    if not versions:
        return

    def _build_versions_table() -> Table:
        table = Table(title="Versions")
        table.add_column("bundle_id", style="cyan")
        table.add_column("Version")
        table.add_column("State")
        table.add_column("Created")
        for v in versions:
            state = v.get("state") or "[dim]—[/dim]"
            style = "dim" if state == "archived" else None
            table.add_row(
                v.get("bundle_id", ""),
                v.get("version", ""),
                state,
                (v.get("created_at") or "")[:10],
                style=style,
            )
        return table

    emit(versions, table=_build_versions_table)


@rulesets_app.command(name="promote-to-live")
def promote_ruleset(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
    ruleset_name: str = typer.Argument(..., help="Ruleset name within the rulebook."),
    ruleset_id: str = typer.Argument(..., help="Specific ruleset version (bundle_id) to promote."),
    note: Optional[str] = typer.Option(
        None,
        "--note",
        help="Optional human-readable note recorded on the resulting RulebookVersion.",
    ),
) -> None:
    """Atomically promote a `testing`-state ruleset version to `live`.

    The candidate ruleset must already be in `testing` state. The
    operation atomically (1) demotes any prior live ruleset of the same
    name to `archived`, (2) promotes the candidate to `live`,
    (3) updates the rulebook's live_ruleset_pins, and (4) cuts a new
    Rulebook version.

    Example::

        aethis rulesets promote-to-live aethis/uk-fsm child_eligibility rs_abc \\
            --note "post-2026-04 statutory update"
    """
    _cfg, client = load_client_or_fallback()
    try:
        resp = client.promote_ruleset_to_live(
            rulebook,
            ruleset_name,
            ruleset_id=ruleset_id,
            note=note,
        )
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    success(f"Promoted ruleset {ruleset_name!r} version {ruleset_id} → live in rulebook {rulebook}")
    console.print(f"  new rulebook version: [green]v{resp.get('new_rulebook_version')}[/green]")
    prior = resp.get("prior_live_archived_id")
    if prior:
        console.print(f"  prior live archived: [dim]{prior}[/dim]")
    console.print(f"  cut reason: [dim]{resp.get('cut_reason')}[/dim]")


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
