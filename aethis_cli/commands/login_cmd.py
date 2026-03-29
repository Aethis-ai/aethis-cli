"""aethis login — store API key in ~/.config/aethis/credentials."""

from __future__ import annotations

import typer
import yaml

from aethis_cli.config import credentials_path
from aethis_cli.output import success


def login(
    api_key: str = typer.Option(None, "--api-key", "-k", help="API key (ak_live_...)"),
) -> None:
    """Store your Aethis API key for use across projects."""
    if not api_key:
        api_key = typer.prompt("Enter your API key")

    creds = credentials_path()
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text(yaml.dump({"api_key": api_key}))
    creds.chmod(0o600)

    success(f"API key saved to {creds}")
