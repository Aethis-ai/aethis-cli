"""aethis publish — activate a generated ruleset."""

from __future__ import annotations

from typing import Optional

import typer

from aethis_cli.config import load_project_config, make_authed_client, resolve_api_key
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel, success


def publish(
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Publish even when tests are failing. Not recommended for production.",
    ),
    slug: Optional[str] = typer.Option(
        None,
        "--slug",
        help=(
            "Optional stable human-readable alias for this ruleset, e.g. "
            "'acme/insurance/car'. Survives regeneration — callers can hit "
            "the slug from /decide and always get the current active ruleset. "
            "Format: lowercase ASCII segments separated by '/'. The 'aethis/*' "
            "namespace is reserved for official rulesets."
        ),
    ),
    rulebook: Optional[str] = typer.Option(
        None,
        "--rulebook",
        help=(
            "Rulebook ID or slug to publish this ruleset into "
            "(converged 2-term model). Requires --ruleset-name. "
            "The produced ruleset lands in state='testing' rather than "
            "being flipped to status='active'; promotion to live then "
            "flows via `aethis rulesets promote-to-live`. Requires "
            "aethis-core v0.21.0+."
        ),
    ),
    ruleset_name: Optional[str] = typer.Option(
        None,
        "--ruleset-name",
        help=(
            "Identifier-style name within the parent rulebook (e.g. "
            "'child_eligibility'). Required when --rulebook is set."
        ),
    ),
) -> None:
    """Publish the latest generated ruleset (make it active for /decide).

    Runs the project's test suite first and refuses to publish if any test
    fails or errors. Use --force to override (for example, when you're
    publishing an intentionally draft ruleset).

    Pass --rulebook + --ruleset-name to publish into a Rulebook (the
    converged 2-term model). The produced ruleset gets stamped with
    those FKs and lands in state='testing'; promote it to live with
    `aethis rulesets promote-to-live <rulebook> <ruleset_name> <rs_id>`.
    """
    if (rulebook is None) != (ruleset_name is None):
        console.print(
            "[red]--rulebook and --ruleset-name must be set together "
            "(or both omitted for legacy publish-to-active).[/red]"
        )
        raise typer.Exit(code=1)
    cfg = load_project_config()
    api_key = resolve_api_key(cfg)
    client = make_authed_client(api_key, cfg.base_url)

    pid = project_id or cfg.project_id
    if not pid:
        console.print("[red]No project_id. Run 'aethis generate' first or pass --project-id.[/red]")
        raise typer.Exit(code=1)

    # Test gate — run golden scenarios first.
    try:
        test_result = client.run_tests(pid)
    except AethisAPIError as e:
        if not force:
            error_panel(e)
            console.print("[yellow]Could not verify tests. Pass --force to publish without verification.[/yellow]")
            raise typer.Exit(code=1)
        console.print("[yellow]Warning: test run failed but --force was used; publishing anyway.[/yellow]")
    else:
        failed = test_result.get("failed", 0)
        errors = test_result.get("errors", 0)
        total = test_result.get("total", 0)
        passed = test_result.get("passed", 0)
        if failed or errors:
            if not force:
                console.print(
                    f"[red]Refusing to publish: {failed} failing, {errors} erroring "
                    f"out of {total} tests ({passed} passing).[/red]"
                )
                console.print(
                    "[dim]Fix failures with 'aethis generate' (after adding guidance), "
                    "or pass --force to override.[/dim]"
                )
                raise typer.Exit(code=1)
            console.print(
                f"[yellow]Warning: publishing with --force despite {failed} failing, {errors} erroring tests.[/yellow]"
            )

    try:
        # Thread --force to the server-side TDD gate (aethis-core 0.11+).
        # Older engines ignore the field; newer ones refuse a publish over
        # failing tests unless force_unsafe=True is explicit, in which case
        # they record a publish_force_bypass audit event.
        result = client.publish(
            pid,
            slug=slug,
            force_unsafe=force,
            rulebook_id=rulebook,
            ruleset_name=ruleset_name,
        )
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    msg = f"Published ruleset {result.get('ruleset_id')}"
    if result.get("slug"):
        msg += f" — slug: {result['slug']}"
    success(msg)
    if result.get("state") == "testing" and result.get("rulebook_id"):
        # Rulebook-mode publish: surface the next step explicitly so
        # users don't wonder why /decide doesn't return their ruleset
        # yet — the ruleset is in `testing`, not `live`.
        console.print(
            f"  rulebook: [cyan]{result['rulebook_id']}[/cyan] · "
            f"ruleset_name: [cyan]{result.get('ruleset_name')}[/cyan] · "
            f"state: [yellow]testing[/yellow]"
        )
        console.print(
            f"  [dim]promote with: aethis rulesets promote-to-live "
            f"{result['rulebook_id']} {result.get('ruleset_name')} "
            f"{result.get('ruleset_id')}[/dim]"
        )
