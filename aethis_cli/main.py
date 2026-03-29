"""aethis CLI — main entry point."""

from __future__ import annotations

import typer

from aethis_cli.commands.init_cmd import init
from aethis_cli.commands.login_cmd import login
from aethis_cli.commands.generate_cmd import generate
from aethis_cli.commands.status_cmd import status
from aethis_cli.commands.test_cmd import test
from aethis_cli.commands.publish_cmd import publish
from aethis_cli.commands.fields_cmd import fields
from aethis_cli.commands.explain_cmd import explain
from aethis_cli.commands.decide_cmd import decide

app = typer.Typer(
    name="aethis",
    help="CLI for the Aethis developer API — author, test, and publish rule bundles.",
    no_args_is_help=True,
)

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
