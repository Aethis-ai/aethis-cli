"""Install the Aethis MCP server without copying credentials into host config."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import typer

from aethis_cli.auth_helpers import RUNTIME, resolve_cached_key
from aethis_cli.config import (
    ANONYMOUS_PROFILE,
    DEFAULT_BASE_URL,
    DEFAULT_PROFILE,
    active_profile_name,
    get_profile,
    load_credentials,
)
from aethis_cli.output import console, info, success, warn

mcp_app = typer.Typer(
    name="mcp",
    help="Install or remove the Aethis MCP server in your editor's config.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
VALID_TARGETS = ("claude-code", "cursor", "claude-desktop", "windsurf", "codex", "all")
_INSTALLABLE_TARGETS = tuple(t for t in VALID_TARGETS if t != "all")
_SERVER_KEY, _MCP_COMMAND, _MCP_ARGS = "aethis", "npx", ["-y", "aethis-mcp@latest"]
_SAFE_ENV_KEYS, _LEGACY_ENV_KEYS = {"AETHIS_PROFILE", "XDG_CONFIG_HOME"}, {"AETHIS_API_KEY", "AETHIS_BASE_URL"}


def _selected_profile_reference() -> tuple[str, dict[str, str]]:
    """Pin a host to the selected non-secret credential reference."""
    profile_name = active_profile_name()
    credentials = load_credentials()
    if (
        profile_name != ANONYMOUS_PROFILE
        and profile_name != DEFAULT_PROFILE
        and profile_name not in credentials["profiles"]
    ):
        raise typer.BadParameter(
            f"Selected profile '{profile_name}' does not exist. Save it with `aethis login --profile {profile_name}` "
            "or select an existing profile before installing MCP."
        )
    selected_profile = {} if profile_name == ANONYMOUS_PROFILE else get_profile(profile_name)
    if profile_name != ANONYMOUS_PROFILE and selected_profile.get("auth_mode", "api_key") != "api_key":
        raise typer.BadParameter(
            f"Selected profile '{profile_name}' uses unsupported auth_mode '{selected_profile['auth_mode']}'. "
            "MCP setup currently supports saved API-key profiles only."
        )
    key_override = RUNTIME.api_key_override or os.environ.get("AETHIS_API_KEY")
    if key_override is not None and key_override != selected_profile.get("api_key"):
        raise typer.BadParameter(
            "The supplied API key does not match the selected saved profile. Save/select a profile, or configure this one-off MCP process environment yourself."
        )
    base_override = RUNTIME.base_url_override or os.environ.get("AETHIS_BASE_URL")
    selected_base_url = selected_profile.get("base_url") or DEFAULT_BASE_URL
    if base_override is not None and base_override != selected_base_url:
        raise typer.BadParameter(
            "The supplied base URL does not match the selected saved profile. Save/select a profile, or configure this one-off MCP process environment yourself."
        )
    # Do not hide a legacy keychain/YAML credential behind a keyless default
    # profile (including one that has only a custom endpoint). It cannot be
    # paired reliably with the selected endpoint until the user saves a named
    # profile with its credential.
    if profile_name != ANONYMOUS_PROFILE and not selected_profile.get("api_key"):
        if resolve_cached_key() is not None:
            raise typer.BadParameter(
                "A legacy Aethis credential was found but the selected default profile has no saved key. "
                "Save and select a named profile with `aethis login --profile <name>` "
                "or `aethis profile add <name> --api-key …` before installing MCP."
            )
    # A genuinely clean installation has no stored ``default`` profile. MCP
    # P2 treats its selector as explicit, so use the reserved keyless selector.
    if profile_name == DEFAULT_PROFILE and DEFAULT_PROFILE not in credentials["profiles"]:
        profile_name = ANONYMOUS_PROFILE
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")).expanduser().resolve()
    return profile_name, {"AETHIS_PROFILE": profile_name, "XDG_CONFIG_HOME": str(config_home)}


def _config_path_for(target: str, *, cwd: Optional[Path] = None, home: Optional[Path] = None) -> Path:
    home, cwd = home or Path.home(), cwd or Path.cwd()
    if target == "claude-code":
        return cwd / ".mcp.json"
    if target == "cursor":
        return home / ".cursor" / "mcp.json"
    if target == "claude-desktop":
        return (
            home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
            if platform.system() == "Darwin"
            else home / ".config" / "Claude" / "claude_desktop_config.json"
        )
    if target == "windsurf":
        return home / ".codeium" / "windsurf" / "mcp_config.json"
    raise ValueError(f"unknown JSON-config target: {target}")


def _server_entry(env: dict[str, str]) -> dict:
    return {"command": _MCP_COMMAND, "args": _MCP_ARGS, "env": env}


def _is_known_generated_entry(entry: object) -> bool:
    if not isinstance(entry, dict) or entry.get("command") != _MCP_COMMAND or entry.get("args") != _MCP_ARGS:
        return False
    env = entry.get("env")
    return isinstance(env, dict) and set(env) in (_SAFE_ENV_KEYS, _LEGACY_ENV_KEYS)


def _legacy_entry_matches_selected_profile(entry: dict, profile_name: str) -> bool:
    """A raw-key legacy host entry may migrate only to its exact saved pair."""
    env = entry.get("env")
    if not isinstance(env, dict) or set(env) != _LEGACY_ENV_KEYS or profile_name == ANONYMOUS_PROFILE:
        return False
    profile = get_profile(profile_name)
    return env.get("AETHIS_API_KEY") == profile.get("api_key") and env.get("AETHIS_BASE_URL") == (
        profile.get("base_url") or DEFAULT_BASE_URL
    )


def _read_config(path: Path) -> dict:
    if not path.exists() or not path.read_text().strip():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f"Could not parse existing config at {path}: {e}. Fix it and re-run.") from None
    if not isinstance(data, dict):
        raise typer.BadParameter(f"Existing config at {path} is not a JSON object.")
    return data


def _write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _expand_targets(target: str) -> list[str]:
    return list(_INSTALLABLE_TARGETS) if target == "all" else [target]


def _validate_target(target: str) -> None:
    if target not in VALID_TARGETS:
        console.print(f"[red]Invalid --target '{target}'.[/red] Must be one of: {', '.join(VALID_TARGETS)}")
        raise typer.Exit(code=1)


@dataclass(frozen=True)
class _JsonInstallPlan:
    path: Path
    data: dict
    changed: bool


def _prepare_json_install(target: str, env: dict[str, str]) -> _JsonInstallPlan:
    """Read and validate one JSON host before any host is changed."""
    path, config = _config_path_for(target), _read_config(_config_path_for(target))
    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise typer.BadParameter(f"Existing mcpServers at {path} is not an object; refusing to replace it.")
    desired, existing = _server_entry(env), servers.get(_SERVER_KEY)
    if existing is not None and existing != desired and not _is_known_generated_entry(existing):
        raise typer.BadParameter(
            f"Existing Aethis MCP entry at {path} was not generated by this CLI; refusing to overwrite it."
        )
    if isinstance(existing, dict) and set(existing.get("env", {})) == _LEGACY_ENV_KEYS:
        if not _legacy_entry_matches_selected_profile(existing, env["AETHIS_PROFILE"]):
            raise typer.BadParameter(
                "A legacy Aethis MCP registration does not match the selected saved profile. "
                "Save and select the matching profile before migrating it."
            )
    changed = existing != desired
    if changed:
        # Host-specific settings such as disabled tools or timeouts belong to
        # the user. Build the exact update now; do not re-read it after other
        # hosts have been modified.
        updated = dict(existing) if isinstance(existing, dict) else {}
        updated.update(desired)
        servers = dict(servers)
        servers[_SERVER_KEY] = updated
        config = dict(config)
        config["mcpServers"] = servers
    return _JsonInstallPlan(path=path, data=config, changed=changed)


def _apply_json_install(plan: _JsonInstallPlan) -> Path:
    if plan.changed:
        _write_config(plan.path, plan.data)
    return plan.path


def _preflight_codex(env: dict[str, str]) -> Optional[dict]:
    """Return the inspected native registration after every refusal check."""
    existing = _codex_get()
    if existing is None or _codex_entry_matches(existing, env):
        return existing
    transport = _codex_transport(existing)
    if not _is_known_generated_entry(transport):
        raise typer.BadParameter(
            "Existing Codex Aethis MCP registration was not generated by this CLI; refusing to overwrite it."
        )
    if _codex_has_preservation_sensitive_settings(existing):
        raise typer.BadParameter(
            "Existing Codex Aethis MCP registration has user settings that native remove/add cannot preserve; update its selected profile manually instead."
        )
    if set(transport.get("env", {})) == _LEGACY_ENV_KEYS and not _legacy_entry_matches_selected_profile(
        transport, env["AETHIS_PROFILE"]
    ):
        raise typer.BadParameter(
            "A legacy Codex Aethis MCP registration does not match the selected saved profile. Save and select the matching profile before migrating it."
        )
    return existing


def _uninstall_one(target: str) -> tuple[Path, bool]:
    path = _config_path_for(target)
    if not path.exists():
        return path, False
    config, servers = _read_config(path), _read_config(path).get("mcpServers", {})
    if not isinstance(servers, dict):
        raise typer.BadParameter(f"Existing mcpServers at {path} is not an object; refusing to change it.")
    existing = servers.get(_SERVER_KEY)
    if existing is None:
        return path, False
    if not _is_known_generated_entry(existing):
        raise typer.BadParameter(
            f"Existing Aethis MCP entry at {path} was not generated by this CLI; refusing to remove it."
        )
    del servers[_SERVER_KEY]
    config["mcpServers"] = servers
    _write_config(path, config)
    return path, True


def _codex_executable() -> str:
    executable = shutil.which("codex")
    if executable is None:
        raise typer.BadParameter(
            "Codex CLI is not installed or is not on PATH. Install Codex, then re-run this command."
        )
    return executable


def _codex(argv: list[str]) -> subprocess.CompletedProcess[str]:
    executable = _codex_executable()
    try:
        return subprocess.run([executable, "mcp", *argv], capture_output=True, text=True, timeout=15, check=False)
    except subprocess.TimeoutExpired as e:
        raise typer.BadParameter(
            "Codex CLI timed out while updating its MCP configuration; no result was confirmed."
        ) from e


def _codex_get() -> Optional[dict]:
    result = _codex(["get", _SERVER_KEY, "--json"])
    if result.returncode != 0:
        output = f"{result.stdout}\n{getattr(result, 'stderr', '')}".lower()
        if "no mcp server named" in output and "found" in output:
            return None
        raise typer.BadParameter("Codex could not inspect its Aethis MCP registration; refusing to change it.")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise typer.BadParameter("Codex returned an unreadable MCP registration; refusing to replace it.") from None
    if not isinstance(data, dict):
        raise typer.BadParameter("Codex returned a non-object MCP registration; refusing to replace it.")
    return data


def _codex_transport(registration: dict) -> dict:
    """Extract Codex's stdio transport without discarding its host envelope."""
    transport = registration.get("transport")
    return transport if isinstance(transport, dict) else registration


def _codex_entry_matches(entry: dict, env: dict[str, str]) -> bool:
    entry = _codex_transport(entry)
    return entry.get("command") == _MCP_COMMAND and entry.get("args") == _MCP_ARGS and entry.get("env") == env


def _codex_has_preservation_sensitive_settings(registration: dict) -> bool:
    """Whether remove/add would discard a visible user restriction/setting."""
    transport = _codex_transport(registration)
    if transport.get("cwd") is not None or transport.get("env_vars") not in (None, []):
        return True
    for key in ("enabled_tools", "disabled_tools", "startup_timeout_sec", "tool_timeout_sec"):
        if registration.get(key) is not None:
            return True
    if registration.get("enabled") is False:
        return True
    # Unknown persisted envelope members may be settings a native remove/add
    # cannot round-trip. Refuse rather than make their loss invisible.
    known = {
        "name",
        "enabled",
        "disabled_reason",
        "transport",
        "enabled_tools",
        "disabled_tools",
        "startup_timeout_sec",
        "tool_timeout_sec",
    }
    if "transport" not in registration:
        known |= {"type", "command", "args", "env", "env_vars", "cwd"}
    return bool(set(registration) - known)


def _install_codex(env: dict[str, str], existing: Optional[dict]) -> str:
    """Apply an already-preflighted native Codex registration plan."""
    if existing is not None:
        if _codex_entry_matches(existing, env):
            return "already configured"
        if _codex(["remove", _SERVER_KEY]).returncode != 0:
            raise typer.BadParameter("Codex could not remove the prior generated Aethis registration.")
        removed_prior = True
    else:
        removed_prior = False
    result = _codex(
        [
            "add",
            _SERVER_KEY,
            "--env",
            f"AETHIS_PROFILE={env['AETHIS_PROFILE']}",
            "--env",
            f"XDG_CONFIG_HOME={env['XDG_CONFIG_HOME']}",
            "--",
            _MCP_COMMAND,
            *_MCP_ARGS,
        ]
    )
    if result.returncode != 0:
        if removed_prior:
            raise typer.BadParameter(
                "Codex removed the prior Aethis MCP registration but could not add its replacement; rerun after fixing Codex."
            )
        raise typer.BadParameter("Codex could not add the Aethis MCP registration.")
    # ``add`` is Codex's native acknowledgement. Do not inspect again here:
    # the preflight snapshot is the only state on which refusal decisions are
    # based, so a late read failure cannot strand JSON hosts after mutation.
    return "configured"


def _uninstall_codex() -> bool:
    existing = _codex_get()
    if existing is None:
        return False
    if not _is_known_generated_entry(_codex_transport(existing)):
        raise typer.BadParameter(
            "Existing Codex Aethis MCP registration was not generated by this CLI; refusing to remove it."
        )
    if _codex(["remove", _SERVER_KEY]).returncode != 0 or _codex_get() is not None:
        raise typer.BadParameter("Codex did not confirm removal of the Aethis MCP registration.")
    return True


@mcp_app.command("install")
def install(target: str = typer.Option(..., "--target", "-t", help=f"One of: {', '.join(VALID_TARGETS)}.")) -> None:
    """Install a selected-profile Aethis MCP registration in one or all hosts."""
    _validate_target(target)
    profile_name, env = _selected_profile_reference()
    targets = _expand_targets(target)
    # ``all`` includes Codex by contract. Check its executable before any JSON
    # host is changed so missing Codex cannot leave a partial installation.
    if "codex" in targets:
        _codex_executable()
    # Build every host plan before mutating any of them. The native remove/add
    # pair remains explicitly non-atomic and reports that state if add fails.
    json_plans: dict[str, _JsonInstallPlan] = {}
    codex_plan: Optional[dict] = None
    for current in targets:
        if current == "codex":
            codex_plan = _preflight_codex(env)
        else:
            json_plans[current] = _prepare_json_install(current, env)
    for current in targets:
        if current == "codex":
            success(f"codex: {_install_codex(env, codex_plan)} Aethis MCP for profile '{profile_name}'")
        else:
            success(
                f"{current}: configured Aethis MCP for profile '{profile_name}' at {_apply_json_install(json_plans[current])}"
            )
    info("Restart the host to pick up the Aethis MCP server.")


@mcp_app.command("uninstall")
def uninstall(target: str = typer.Option(..., "--target", "-t", help=f"One of: {', '.join(VALID_TARGETS)}.")) -> None:
    """Remove only known Aethis registrations, leaving user entries untouched."""
    _validate_target(target)
    targets = _expand_targets(target)
    if "codex" in targets:
        _codex_executable()
    any_removed = False
    for current in targets:
        path, removed = ("Codex", _uninstall_codex()) if current == "codex" else _uninstall_one(current)
        if removed:
            success(f"{current}: removed Aethis MCP registration from {path}")
            any_removed = True
        else:
            warn(f"{current}: no Aethis MCP registration found at {path} (nothing to do)")
    if not any_removed:
        info("No Aethis registrations were present.")
