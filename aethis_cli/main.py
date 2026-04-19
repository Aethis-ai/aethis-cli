"""aethis CLI — main entry point."""

from __future__ import annotations

import sys
from importlib.metadata import entry_points
from typing import Optional

import typer

from aethis_cli._version import __version__
from aethis_cli.errors import AethisAPIError, AuthenticationError, ConfigError
from aethis_cli.output import console
from aethis_cli.commands.account_cmd import account_app
from aethis_cli.commands.bundles_cmd import bundles_app
from aethis_cli.commands.guidance_cmd import guidance_app
from aethis_cli.commands.projects_cmd import projects_app
from aethis_cli.commands.init_cmd import init
from aethis_cli.commands.login_cmd import login
from aethis_cli.commands.generate_cmd import generate
from aethis_cli.commands.status_cmd import status
from aethis_cli.commands.test_cmd import test
from aethis_cli.commands.publish_cmd import publish
from aethis_cli.commands.fields_cmd import fields
from aethis_cli.commands.explain_cmd import explain
from aethis_cli.commands.decide_cmd import decide
from aethis_cli.commands.whoami_cmd import whoami


PLUGIN_GROUP = "aethis_cli.plugins"


def _format_error_detail(detail: object) -> str:
    if isinstance(detail, dict):
        reason = detail.get("reason_code", "unknown")
        action = detail.get("action", "unknown")
        missing = detail.get("missing_permissions", [])
        missing_str = ", ".join(missing) if isinstance(missing, list) else str(missing)
        message = detail.get("message") or detail.get("error") or "Request denied"
        return f"{message} (reason={reason}, action={action}, missing={missing_str})"
    return str(detail)


def _version_callback(value: bool) -> None:
    if value:
        print(f"aethis {__version__}")
        raise typer.Exit()


_APP_HELP = """
Author, test, and publish rule bundles via the Aethis developer API.

Common flows:

    aethis status                       # what server / key / project am I on?
    aethis projects list                # see all projects + latest bundles
    aethis explain -b <bundle>          # human-readable rules for a bundle
    aethis decide -b <bundle> -i '{"age": 21}'   # evaluate eligibility

Authoring (invite-only beta):

    aethis init                         # scaffold a new project dir
    aethis generate --poll              # generate + poll until done
    aethis test && aethis publish       # gate on tests, then publish

Targeting a different server:

    AETHIS_BASE_URL=http://localhost:8080 aethis projects list

Run `aethis <command> --help` for per-command examples.
"""


app = typer.Typer(
    name="aethis",
    help=_APP_HELP,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


@app.callback(invoke_without_command=True)
def main(
    version: Optional[bool] = typer.Option(
        None, "--version", "-V", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """CLI for the Aethis developer API — author, test, and publish rule bundles."""


app.add_typer(account_app, name="account")
app.add_typer(bundles_app, name="bundles")
app.add_typer(guidance_app, name="guidance")
app.add_typer(projects_app, name="projects")
app.command()(init)
app.command()(login)
app.command()(generate)
app.command()(status)
app.command(name="test")(test)
app.command()(publish)
app.command()(fields)
app.command()(explain)
app.command()(decide)
app.command()(whoami)


def _load_plugins(target: typer.Typer) -> None:
    """Discover and register third-party plugins.

    A plugin declares an entry point in the `aethis_cli.plugins` group pointing
    at a callable `register(app: typer.Typer) -> None`. Plugins are loaded on
    every invocation; a failure in one plugin prints a warning but never breaks
    the CLI.
    """
    try:
        plugins = entry_points(group=PLUGIN_GROUP)
    except Exception:
        return
    for ep in plugins:
        try:
            register = ep.load()
            register(target)
        except Exception as exc:
            print(f"[aethis] plugin {ep.name!r} failed to load: {exc}", file=sys.stderr)


_load_plugins(app)


def cli() -> None:
    """Entry point wrapper that catches config/auth errors cleanly."""
    try:
        app()
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)
    except AuthenticationError as e:
        console.print(f"[red]Auth error:[/red] {e}")
        raise SystemExit(1)
    except AethisAPIError as e:
        detail = _format_error_detail(e.detail)
        console.print(f"[red]Error: {detail} (HTTP {e.status_code})[/red]", highlight=False)
        if e.status_code == 401:
            console.print("[dim]Run 'aethis login' to re-authenticate.[/dim]")
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
