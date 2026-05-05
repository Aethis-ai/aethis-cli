"""Shared helpers for validating ID arguments."""

from __future__ import annotations

from typing import Literal

import typer

from aethis_cli.output import console

IdKind = Literal["ruleset", "slug", "project", "unknown"]


def classify_id(value: str) -> IdKind:
    """Classify a user-supplied ruleset reference by shape.

    Ruleset IDs:  `<section>:<yyyymmdd>-<sha>` (contain a colon).
    Slugs:       `<namespace>/<name>` or deeper (contain `/`, no colon).
    Project IDs: start with `proj_`.
    """
    if value.startswith("proj_"):
        return "project"
    if ":" in value:
        return "ruleset"
    if "/" in value:
        return "slug"
    return "unknown"


def require_ruleset_id(value: str) -> None:
    """Exit with a helpful message if `value` is not a ruleset ID or slug.

    The public API resolves both ruleset IDs and slugs on `/decide`,
    `/schema`, and `/explain`, so both are accepted here.
    """
    kind = classify_id(value)
    if kind in ("ruleset", "slug"):
        return
    if kind == "project":
        console.print(
            f"[red]'{value}' looks like a Project ID, not a Ruleset ID.[/red]\n"
            "[dim]Pass the 'Ruleset' column value from `aethis projects list` "
            "(e.g. example_ruleset:20260408-abc1234), or a slug like "
            "aethis/uk-fsm/universal-infant.[/dim]"
        )
    else:
        console.print(
            f"[red]'{value}' is not a valid Ruleset ID or slug.[/red]\n"
            "[dim]Ruleset IDs look like <name>:<yyyymmdd>-<sha>. "
            "Slugs look like <namespace>/<name> (e.g. aethis/uk-fsm/universal-infant). "
            "See `aethis rulesets list` for available rulesets.[/dim]"
        )
    raise typer.Exit(code=1)
