"""aethis publish — activate a generated ruleset."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from aethis_cli.config import load_project_config, make_authed_client, resolve_api_key
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel, success, warn
from aethis_cli.source_targets import (
    SourceTargetsError,
    load_source_targets,
    resolve_source_targets,
)


def _print_resolutions(resolutions: list) -> None:
    """Show what each citation key resolved to, keeping the two reference
    kinds visibly distinct.

    A URL citation becomes a schema-v1 reference anyone can follow; a file
    citation becomes a schema-v2 artefact reference whose download route is
    authenticated. Rendering them identically would invite an author to
    publish a "link" that nobody outside the project can open.
    """
    console.print(f"\n[bold]Source targets[/bold] ({len(resolutions)})")
    for res in resolutions:
        if res.kind == "artefact":
            note = " [dim](reused existing upload — identical bytes)[/dim]" if res.reused else ""
            console.print(
                f"  [cyan]{res.key}[/cyan]  [magenta]artefact[/magenta] {res.detail}{note}",
                highlight=False,
            )
            console.print(
                "    [dim]uploaded snapshot, verified at publish — authenticated download, "
                "never anonymously readable[/dim]",
                highlight=False,
            )
        else:
            console.print(
                f"  [cyan]{res.key}[/cyan]  [green]url[/green] {res.detail}",
                highlight=False,
            )
            console.print(
                "    [dim]fetched and snapshotted at publish — public link[/dim]",
                highlight=False,
            )


def _verify_citations_landed(client, ruleset_id: Optional[str], sent_keys: set) -> None:
    """Report how many of the supplied citation targets actually landed.

    The engine resolves only the citation keys the COMPILED ruleset declares
    in its criteria' ``source_refs``, and silently ignores any target whose
    key nothing declares. So a targets file with a typo'd key — or a ruleset
    that declares no citations at all — publishes successfully with zero
    citations attached, after uploading the files. That is the worst kind of
    quiet: the author believes the ruleset is cited and it is not.

    The publish response carries no citation count, so this OBSERVES the
    result by reading the published ruleset back rather than inferring it.
    A read-back that fails must never turn a successful publish into a
    failure — it degrades to an honest "could not verify" line.
    """
    if not sent_keys:
        return
    if not ruleset_id:
        console.print("[yellow]![/yellow] Could not verify citations: the publish returned no ruleset_id.")
        return
    try:
        explained = client.explain(ruleset_id) or {}
    except Exception as exc:  # noqa: BLE001 - never fail a successful publish
        console.print(
            f"[dim]Could not read the ruleset back to verify citations ({exc}). "
            f"Check with: aethis explain --ruleset-id {ruleset_id}[/dim]"
        )
        return

    landed = {
        str(ref.get("source_id"))
        for criterion in (explained.get("criteria") or [])
        if isinstance(criterion, dict)
        for ref in (criterion.get("source_references") or [])
        if isinstance(ref, dict) and ref.get("source_id")
    }
    if landed == sent_keys:
        console.print(f"[dim]{len(landed)} citation(s) attached to the published ruleset.[/dim]")
        return

    missing = sorted(sent_keys - landed)
    warn(f"{len(landed)} of {len(sent_keys)} supplied citation target(s) landed on the published ruleset.")
    if missing:
        console.print(
            "  Not attached: " + ", ".join(missing),
            style="yellow",
            markup=False,
            highlight=False,
        )
        console.print(
            "  [dim]The engine only resolves citation keys the compiled ruleset declares in "
            "its criteria (source_refs). Check the keys match, and that generation produced "
            "criteria carrying them.[/dim]"
        )


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
    source_targets: Optional[Path] = typer.Option(
        None,
        "--source-targets",
        help=(
            "Path to a YAML or JSON file resolving the ruleset's citation "
            "keys to the documents they cite. Each entry names exactly one "
            "of 'url' (a public HTTPS document) or 'file' (a local file, "
            "uploaded to the project and cited as a retained artefact), plus "
            "title, authority, licence and the verbatim 'quote.exact' text. "
            "The engine verifies every quote against the source bytes at "
            "publish time and refuses the publish if any citation fails."
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
    # Local validation FIRST — before config, credentials or any HTTP. A
    # malformed targets file must cost zero round trips and must never upload
    # half a citation set before failing.
    targets = []
    if source_targets is not None:
        try:
            targets = load_source_targets(source_targets)
        except SourceTargetsError as e:
            console.print(f"[red]{e}[/red]", highlight=False)
            console.print(
                "[dim]Each entry needs exactly one of 'url' or 'file', plus "
                "title, authority, licence and quote.exact.[/dim]"
            )
            raise typer.Exit(code=1)

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

    # Resolve citation targets: URL entries pass through untouched; file
    # entries are uploaded (or matched by sha256 against an identical source
    # already in the project) and cited by their source_id.
    wire_targets: dict = {}
    uploaded_any = False
    if targets:
        try:
            wire_targets, resolutions = resolve_source_targets(client, pid, targets)
        except SourceTargetsError as e:
            console.print(f"[red]{e}[/red]", highlight=False)
            raise typer.Exit(code=1)
        except AethisAPIError as e:
            error_panel(e)
            console.print("[yellow]Could not resolve source targets; nothing was published.[/yellow]")
            raise typer.Exit(code=1)
        uploaded_any = any(r.kind == "artefact" for r in resolutions)
        _print_resolutions(resolutions)

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
            source_targets=wire_targets or None,
        )
    except AethisAPIError as e:
        error_panel(e)
        if uploaded_any:
            # The publish is fail-closed, but the uploads that preceded it are
            # not rolled back. Say so plainly: without this, a failed publish
            # reads as "my files went nowhere", and the natural next move —
            # re-running — looks like it will duplicate them. It won't; the
            # sha256 match reuses them.
            console.print(
                "[dim]Files uploaded for citations remain in the project and will be "
                "reused (matched by sha256) when you retry — retrying does not "
                "duplicate them.[/dim]"
            )
        raise typer.Exit(code=1)

    msg = f"Published ruleset {result.get('ruleset_id')}"
    if result.get("slug"):
        msg += f" — slug: {result['slug']}"
    success(msg)
    _verify_citations_landed(client, result.get("ruleset_id"), set(wire_targets))
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
