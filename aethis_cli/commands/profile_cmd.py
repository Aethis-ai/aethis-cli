"""aethis profile — manage named credential profiles (admin/dev personas).

Profiles let you keep multiple API keys side by side and switch between them
either per-invocation (``aethis --profile new-dev rulesets list``) or
stickily (``aethis profile use new-dev``). The reserved name ``anonymous``
forces unsigned mode so you can see exactly what a fresh signup would see.

The credentials are stored at ``~/.config/aethis/credentials`` (mode 0600);
see :mod:`aethis_cli.config` for the file format.
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from aethis_cli.config import (
    ANONYMOUS_PROFILE,
    DEFAULT_PROFILE,
    active_profile_name,
    get_profile,
    load_credentials,
    remove_profile,
    set_active_profile,
    set_profile,
)
from aethis_cli.errors import ConfigError
from aethis_cli.output import console, success
from aethis_cli.render import emit, is_json_requested

profile_app = typer.Typer(
    name="profile",
    help="Manage named credential profiles (switch between admin / dev personas).",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _mask_key(api_key: Optional[str]) -> str:
    if not api_key:
        return "[dim](unsigned)[/dim]"
    if len(api_key) <= 12:
        return "***"
    return f"{api_key[:7]}…{api_key[-4:]}"


@profile_app.command(name="list")
def list_profiles() -> None:
    """Show all configured profiles and which one is active."""
    creds = load_credentials()
    active = active_profile_name()
    profiles = creds["profiles"]

    # Always surface the reserved 'anonymous' slot so users know it's available
    # even before they configure a real profile.
    names = sorted(set(profiles.keys()) | {DEFAULT_PROFILE, ANONYMOUS_PROFILE})

    # Structured form used by --output json. Never leaks the raw key.
    structured = [
        {
            "name": name,
            "active": name == active,
            "has_key": bool(profiles.get(name, {}).get("api_key")) and name != ANONYMOUS_PROFILE,
            "key_preview": (None if name == ANONYMOUS_PROFILE else _mask_key(profiles.get(name, {}).get("api_key"))),
            "base_url": profiles.get(name, {}).get("base_url"),
            "auth_mode": profiles.get(name, {}).get("auth_mode") or "api_key",
        }
        for name in names
    ]

    def _build_profile_table() -> Table:
        table = Table(title="Profiles")
        table.add_column("", width=1)
        table.add_column("Name", style="cyan")
        table.add_column("API Key")
        table.add_column("Base URL")
        for name in names:
            profile = profiles.get(name, {})
            marker = "*" if name == active else ""
            if name == ANONYMOUS_PROFILE:
                key_display = "[dim](no key — anonymous mode)[/dim]"
            else:
                key_display = _mask_key(profile.get("api_key"))
            base_display = profile.get("base_url", "[dim](default)[/dim]")
            table.add_row(marker, name, key_display, base_display)
        return table

    emit(structured, table=_build_profile_table)
    if not is_json_requested():
        console.print(f"\nActive: [cyan]{active}[/cyan]")


@profile_app.command(name="use")
def use_profile(name: str = typer.Argument(..., help="Profile name to make sticky")) -> None:
    """Set the sticky default profile.

    The new profile takes effect for all subsequent ``aethis`` invocations
    unless overridden by ``--profile`` or ``AETHIS_PROFILE``.
    """
    if name != ANONYMOUS_PROFILE and not get_profile(name):
        console.print(
            f"[yellow]Profile '{name}' is empty (no API key set).[/yellow] "
            f"Run `aethis login --profile {name}` to populate it."
        )
    try:
        set_active_profile(name)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
    success(f"Active profile is now '{name}'.")


@profile_app.command(name="add")
def add_profile(
    name: str = typer.Argument(..., help="Profile name (e.g. 'admin', 'new-dev')"),
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        "-k",
        help="API key for this profile (omit to OAuth-login later via `aethis login --profile <name>`).",
    ),
    base_url: Optional[str] = typer.Option(
        None,
        "--base-url",
        help="Override the API base URL for this profile (e.g. http://localhost:8080).",
    ),
) -> None:
    """Create or update a named profile."""
    try:
        set_profile(name, api_key=api_key, base_url=base_url)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
    if api_key:
        success(f"Profile '{name}' saved.")
    else:
        success(f"Profile '{name}' created without a key. Run `aethis login --profile {name}` to authenticate.")


@profile_app.command(name="remove")
def remove_profile_cmd(
    name: str = typer.Argument(..., help="Profile name to delete"),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Allow removing the currently active profile (resets active to 'default').",
    ),
) -> None:
    """Delete a profile from the credentials file."""
    if name == ANONYMOUS_PROFILE:
        console.print(
            f"[yellow]'{ANONYMOUS_PROFILE}' is a reserved profile, not stored on disk — nothing to remove.[/yellow]"
        )
        return
    if name == active_profile_name() and not force:
        console.print(
            f"[red]Refusing to remove active profile '{name}'.[/red] "
            "Switch with `aethis profile use <other>` first, or pass --force."
        )
        raise typer.Exit(code=1)
    try:
        remove_profile(name)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
    success(f"Profile '{name}' removed.")
