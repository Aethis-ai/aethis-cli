"""aethis update — update the CLI to the latest release."""

from __future__ import annotations

import json
import subprocess
import sys

import typer

from aethis_cli._version import __version__
from aethis_cli.output import console
from aethis_cli.update_check import (
    _detect_install_method,
    _fetch_latest_pypi,
    _is_newer,
    _save_cache,
)

PACKAGE = "aethis-cli"


def _is_editable_install(package: str) -> bool:
    """True when the package was installed editable (a development checkout)."""
    try:
        from importlib.metadata import distribution

        raw = distribution(package).read_text("direct_url.json")
    except Exception:
        return False
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    dir_info = data.get("dir_info")
    return isinstance(dir_info, dict) and bool(dir_info.get("editable"))


def _upgrade_argv(method: str, package: str) -> list[str]:
    """Map a detected install method to the argv that upgrades it."""
    if method == "uv":
        # `uv tool upgrade` honours the install receipt, so extra
        # `--with` requirements (e.g. staff plugins) are preserved.
        # `uv tool install --upgrade` would re-resolve without them.
        return ["uv", "tool", "upgrade", package]
    if method == "pipx":
        return ["pipx", "upgrade", package]
    return [sys.executable, "-m", "pip", "install", "--upgrade", package]


def update(
    check: bool = typer.Option(
        False,
        "--check",
        help="Only check whether a newer release exists; don't install anything.",
    ),
) -> None:
    """Update the aethis CLI to the latest release.

    Detects how the CLI was installed (uv tool, pipx, or pip) and runs the
    matching upgrade command. Use --check to see what's available without
    changing anything.
    """
    console.print(f"[dim]Current version: {__version__}[/dim]")
    latest = _fetch_latest_pypi(PACKAGE)
    if latest is None:
        console.print(
            "[red]Error:[/red] could not reach PyPI to look up the latest release. "
            "Check your network connection and try again."
        )
        raise typer.Exit(code=1)

    # Refresh the banner cache so the exit-time notice agrees with what we
    # just learned (and goes quiet after a successful update).
    _save_cache(latest)

    if not _is_newer(latest, __version__):
        console.print(f"[green]✓ Already up to date[/green] (aethis-cli {__version__}).")
        return

    console.print(f"[bold]New release available:[/bold] {__version__} → {latest}")

    if check:
        console.print("[dim]Run `aethis update` to install it.[/dim]")
        return

    if _is_editable_install(PACKAGE):
        console.print(
            "[yellow]This looks like a development (editable) install.[/yellow]\n"
            "[dim]Update your checkout instead: git pull && uv sync[/dim]"
        )
        raise typer.Exit(code=1)

    method, _ = _detect_install_method(PACKAGE)
    argv = _upgrade_argv(method, PACKAGE)
    console.print(f"[dim]Running: {' '.join(argv)}[/dim]")
    try:
        result = subprocess.run(argv)
    except FileNotFoundError:
        console.print(
            f"[red]Error:[/red] `{argv[0]}` not found on PATH. "
            f"Upgrade manually with your installer of choice."
        )
        raise typer.Exit(code=1)
    if result.returncode != 0:
        console.print(f"[red]Upgrade command failed[/red] (exit code {result.returncode}).")
        raise typer.Exit(code=result.returncode)

    console.print(f"[green]✓ Updated[/green] aethis-cli {__version__} → {latest}.")
