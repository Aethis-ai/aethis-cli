"""aethis CLI — main entry point."""

from __future__ import annotations

from typing import Optional

import typer

from aethis_cli._version import __version__
from aethis_cli.commands.account_cmd import account_app
from aethis_cli.commands.bundles_cmd import bundles_app
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


def _version_callback(value: bool) -> None:
    if value:
        print(f"aethis {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="aethis",
    help="CLI for the Aethis developer API — author, test, and publish rule bundles.",
    no_args_is_help=True,
)


@app.callback(invoke_without_command=True)
def main(
    version: Optional[bool] = typer.Option(None, "--version", "-V", callback=_version_callback, is_eager=True,
                                           help="Show version and exit."),
) -> None:
    """CLI for the Aethis developer API — author, test, and publish rule bundles."""


app.add_typer(account_app, name="account")
app.add_typer(bundles_app, name="bundles")
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

if __name__ == "__main__":
    app()
