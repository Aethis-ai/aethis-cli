"""aethis login — store API key securely."""

from __future__ import annotations

import typer
import yaml

from aethis_cli.config import credentials_path
from aethis_cli.output import info, success

_KEYRING_SERVICE = "aethis-cli"
_KEYRING_USERNAME = "api_key"


def _save_to_keyring(api_key: str) -> bool:
    """Attempt to store key in OS keychain. Returns True on success."""
    try:
        import keyring

        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, api_key)
        return True
    except Exception:
        return False


def _save_to_file(api_key: str) -> None:
    """Fallback: store key in ~/.config/aethis/credentials (0600)."""
    creds = credentials_path()
    creds.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    creds.write_text(yaml.dump({"api_key": api_key}))
    creds.chmod(0o600)


def login(
    api_key: str = typer.Option(None, "--api-key", "-k", help="API key (ak_live_...)"),
) -> None:
    """Store your Aethis API key for use across projects."""
    if not api_key:
        api_key = typer.prompt("Enter your API key")

    if _save_to_keyring(api_key):
        success("API key saved to system keychain")
    else:
        _save_to_file(api_key)
        info("keyring not available — key saved to file (install 'keyring' for OS keychain)")
        success(f"API key saved to {credentials_path()}")
