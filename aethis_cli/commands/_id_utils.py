"""Shared helpers for validating ID arguments."""

from __future__ import annotations

from typing import Literal

import typer

from aethis_cli.output import console

IdKind = Literal["bundle", "project", "unknown"]


def classify_id(value: str) -> IdKind:
    """Classify a user-supplied ID by shape.

    Bundle IDs look like `<section>:<yyyymmdd>-<sha>` (contain a colon).
    Project IDs start with `proj_`.
    """
    if value.startswith("proj_"):
        return "project"
    if ":" in value:
        return "bundle"
    return "unknown"


def require_bundle_id(value: str) -> None:
    """Exit with a helpful message if `value` is not a bundle-shaped ID."""
    kind = classify_id(value)
    if kind == "bundle":
        return
    if kind == "project":
        console.print(
            f"[red]'{value}' looks like a Project ID, not a Bundle ID.[/red]\n"
            "[dim]Pass the 'Bundle' column value from `aethis projects list` "
            "(e.g. example_bundle:20260408-abc1234).[/dim]"
        )
    else:
        console.print(
            f"[red]'{value}' is not a valid Bundle ID.[/red]\n"
            "[dim]Bundle IDs look like <name>:<yyyymmdd>-<sha>. "
            "See `aethis projects list` or `aethis bundles list`.[/dim]"
        )
    raise typer.Exit(code=1)
