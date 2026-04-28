"""Shared helpers for validating ID arguments."""

from __future__ import annotations

from typing import Literal

import typer

from aethis_cli.output import console

IdKind = Literal["bundle", "slug", "project", "unknown"]


def classify_id(value: str) -> IdKind:
    """Classify a user-supplied bundle reference by shape.

    Bundle IDs:  `<section>:<yyyymmdd>-<sha>` (contain a colon).
    Slugs:       `<namespace>/<name>` or deeper (contain `/`, no colon).
    Project IDs: start with `proj_`.
    """
    if value.startswith("proj_"):
        return "project"
    if ":" in value:
        return "bundle"
    if "/" in value:
        return "slug"
    return "unknown"


def require_bundle_id(value: str) -> None:
    """Exit with a helpful message if `value` is not a bundle ID or slug.

    The public API resolves both bundle IDs and slugs on `/decide`,
    `/schema`, and `/explain`, so both are accepted here.
    """
    kind = classify_id(value)
    if kind in ("bundle", "slug"):
        return
    if kind == "project":
        console.print(
            f"[red]'{value}' looks like a Project ID, not a Bundle ID.[/red]\n"
            "[dim]Pass the 'Bundle' column value from `aethis projects list` "
            "(e.g. example_bundle:20260408-abc1234), or a slug like "
            "aethis/uk-fsm/universal-infant.[/dim]"
        )
    else:
        console.print(
            f"[red]'{value}' is not a valid Bundle ID or slug.[/red]\n"
            "[dim]Bundle IDs look like <name>:<yyyymmdd>-<sha>. "
            "Slugs look like <namespace>/<name> (e.g. aethis/uk-fsm/universal-infant). "
            "See `aethis bundles list` for available bundles.[/dim]"
        )
    raise typer.Exit(code=1)
