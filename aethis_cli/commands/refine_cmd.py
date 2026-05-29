"""aethis refine — add an optional hint, then make the minimal edit to fix failing
tests (finding-driven incremental re-authoring, seeded from the active ruleset)."""

from __future__ import annotations

from typing import Optional

import typer

from aethis_cli.commands.generate_cmd import _run_generate


def refine(
    hint: Optional[str] = typer.Option(
        None, "--hint", help="Guidance hint to add before refining"
    ),
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p"),
    seed_ruleset_id: Optional[str] = typer.Option(
        None, "--seed-ruleset-id",
        help="Ruleset to seed from (defaults to the section's active ruleset)",
    ),
    poll: bool = typer.Option(True, "--poll/--no-poll", help="Poll until refinement completes"),
    timeout: int = typer.Option(600, "--timeout", "-t", help="Polling timeout in seconds"),
) -> None:
    """Refine the active ruleset: add an optional --hint, then make the minimal
    edit to fix failing test cases (seeded from the section's active ruleset)
    rather than re-authoring the whole section. Use ``aethis generate`` for a
    from-scratch build.
    """
    _run_generate(
        project_id=project_id, poll=poll, timeout=timeout,
        mode="refine", seed_ruleset_id=seed_ruleset_id, extra_hint=hint,
    )
