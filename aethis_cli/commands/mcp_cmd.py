"""aethis mcp — wire up the Aethis MCP server in supported editors/clients.

Today users have to hand-edit a different JSON file for each editor
(Cursor, Claude Code, Claude Desktop, Windsurf). This command does the
edit for them: drops in the canonical `aethis` server entry, preserves
any other servers the user already configured, and is idempotent.

The server is invoked as `npx -y aethis-mcp@latest` and reads
`AETHIS_API_KEY` (and optionally `AETHIS_BASE_URL`) from env. We pull
both from the same credential store `aethis status` reads, so the user
only has to run `aethis login` once.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Optional

import typer

from aethis_cli.config import DEFAULT_BASE_URL
from aethis_cli.output import console, info, success, warn

mcp_app = typer.Typer(
    name="mcp",
    help="Install or remove the Aethis MCP server in your editor's config.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


VALID_TARGETS = ("claude-code", "cursor", "claude-desktop", "windsurf", "all")
_INSTALLABLE_TARGETS = tuple(t for t in VALID_TARGETS if t != "all")
_SERVER_KEY = "aethis"


def _resolve_api_key_silent() -> Optional[str]:
    """Resolve the cached API key the same way `aethis status` does.

    Order: AETHIS_API_KEY env var → OS keyring → credentials file.
    Returns None if none is set; the caller decides how to fail.
    """
    key = os.environ.get("AETHIS_API_KEY")
    if key:
        return key

    try:
        import keyring  # type: ignore[import-not-found]

        cached = keyring.get_password("aethis-cli", "api_key")
        if cached:
            return cached
    except Exception:
        pass

    import yaml  # type: ignore[import-untyped]

    xdg = os.environ.get("XDG_CONFIG_HOME")
    creds_path = Path(xdg) / "aethis" / "credentials" if xdg else Path.home() / ".config" / "aethis" / "credentials"
    if creds_path.exists():
        try:
            raw = yaml.safe_load(creds_path.read_text()) or {}
            cached = raw.get("api_key")
            if cached:
                return cached
        except Exception:
            pass
    return None


def _resolve_base_url() -> str:
    """Pick the AETHIS_BASE_URL we want the MCP server to talk to.

    Env override wins; otherwise default to the public endpoint. We
    deliberately do NOT walk up looking for an aethis.yaml here — the
    MCP config is global to the editor, not project-scoped.
    """
    return os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)


def _config_path_for(target: str, *, cwd: Optional[Path] = None, home: Optional[Path] = None) -> Path:
    """Return the absolute config path each client expects."""
    home = home or Path.home()
    cwd = cwd or Path.cwd()

    if target == "claude-code":
        # Project-level — lives next to your repo, not in $HOME.
        return cwd / ".mcp.json"
    if target == "cursor":
        return home / ".cursor" / "mcp.json"
    if target == "claude-desktop":
        if platform.system() == "Darwin":
            return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        # Linux + everything else (Windows users are on their own for now).
        return home / ".config" / "Claude" / "claude_desktop_config.json"
    if target == "windsurf":
        # Codeium's documented path; ~/.windsurf/mcp.json was the old one.
        return home / ".codeium" / "windsurf" / "mcp_config.json"
    raise ValueError(f"unknown target: {target}")


def _server_entry(api_key: str, base_url: str) -> dict:
    """The canonical aethis MCP server stanza."""
    return {
        "command": "npx",
        "args": ["-y", "aethis-mcp@latest"],
        "env": {
            "AETHIS_API_KEY": api_key,
            "AETHIS_BASE_URL": base_url,
        },
    }


def _read_config(path: Path) -> dict:
    """Read an existing client config, tolerating absent/empty files."""
    if not path.exists():
        return {}
    raw = path.read_text().strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise typer.BadParameter(
            f"Could not parse existing config at {path}: {e}. Fix it or delete the file and re-run."
        ) from None
    if not isinstance(data, dict):
        raise typer.BadParameter(f"Existing config at {path} is not a JSON object.")
    return data


def _write_config(path: Path, data: dict) -> None:
    """Persist the config, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _expand_targets(target: str) -> list[str]:
    if target == "all":
        return list(_INSTALLABLE_TARGETS)
    return [target]


def _validate_target(target: str) -> None:
    if target not in VALID_TARGETS:
        valid = ", ".join(VALID_TARGETS)
        console.print(f"[red]Invalid --target '{target}'.[/red] Must be one of: {valid}")
        raise typer.Exit(code=1)


def _install_one(target: str, api_key: str, base_url: str) -> Path:
    """Insert (or update) the aethis entry in one client's config."""
    path = _config_path_for(target)
    config = _read_config(path)

    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[_SERVER_KEY] = _server_entry(api_key, base_url)
    config["mcpServers"] = servers

    _write_config(path, config)
    return path


def _uninstall_one(target: str) -> tuple[Path, bool]:
    """Remove only the aethis entry. Returns (path, was_present)."""
    path = _config_path_for(target)
    if not path.exists():
        return path, False

    config = _read_config(path)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict) or _SERVER_KEY not in servers:
        return path, False

    del servers[_SERVER_KEY]
    config["mcpServers"] = servers
    _write_config(path, config)
    return path, True


@mcp_app.command("install")
def install(
    target: str = typer.Option(
        ...,
        "--target",
        "-t",
        help=f"Which client to wire up. One of: {', '.join(VALID_TARGETS)}.",
    ),
) -> None:
    """Install the Aethis MCP server in one (or all) editor configs.

    Examples:

        aethis mcp install --target cursor
        aethis mcp install --target claude-code
        aethis mcp install --target all
    """
    _validate_target(target)

    api_key = _resolve_api_key_silent()
    if api_key is None:
        console.print("[red]No API key found.[/red] Run `aethis login` first.")
        raise typer.Exit(code=1)

    base_url = _resolve_base_url()
    targets = _expand_targets(target)

    info(f"Using AETHIS_BASE_URL={base_url}")
    for t in targets:
        path = _install_one(t, api_key, base_url)
        success(f"{t}: wrote aethis MCP server to {path}")

    console.print()
    console.print(
        "[dim]Restart your editor to pick up the new MCP server. "
        "If something looks off, re-run with --target <client> after fixing creds.[/dim]"
    )


@mcp_app.command("uninstall")
def uninstall(
    target: str = typer.Option(
        ...,
        "--target",
        "-t",
        help=f"Which client to clean up. One of: {', '.join(VALID_TARGETS)}.",
    ),
) -> None:
    """Remove the Aethis MCP server entry. Leaves other servers untouched.

    Examples:

        aethis mcp uninstall --target cursor
        aethis mcp uninstall --target all
    """
    _validate_target(target)

    targets = _expand_targets(target)
    any_removed = False
    for t in targets:
        path, removed = _uninstall_one(t)
        if removed:
            success(f"{t}: removed aethis entry from {path}")
            any_removed = True
        else:
            warn(f"{t}: no aethis entry found at {path} (nothing to do)")

    if not any_removed:
        # Not an error — uninstall is idempotent — but signal it clearly.
        info("No aethis entries were present.")
