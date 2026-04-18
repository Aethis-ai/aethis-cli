"""aethis publish — activate a generated bundle."""

from __future__ import annotations

from typing import Optional

import typer

from aethis_cli.client import AethisClient
from aethis_cli.config import load_project_config, read_state, resolve_api_key
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
            "Optional stable human-readable alias for this bundle, e.g. "
            "'acme/insurance/car'. Survives regeneration — callers can hit "
            "the slug from /decide and always get the current active bundle. "
            "Format: lowercase ASCII segments separated by '/'. The 'aethis/*' "
            "namespace is reserved for official bundles."
        ),
    ),
) -> None:
    """Publish the latest generated bundle (make it active for /decide).

    Runs the project's test suite first and refuses to publish if any test
    fails or errors. Use --force to override (for example, when you're
    publishing an intentionally draft bundle).
    """
    cfg = load_project_config()
    api_key = resolve_api_key(cfg)
    client = AethisClient(api_key, cfg.base_url)

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
            console.print(
                "[yellow]Could not verify tests. Pass --force to publish without verification.[/yellow]"
            )
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
                f"[yellow]Warning: publishing with --force despite {failed} failing, "
                f"{errors} erroring tests.[/yellow]"
            )

    try:
        result = client.publish(pid, slug=slug)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    msg = f"Published bundle {result.get('bundle_id')}"
    if result.get("slug"):
        msg += f" — slug: {result['slug']}"
    success(msg)
