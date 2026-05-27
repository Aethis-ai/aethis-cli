"""aethis status — show current CLI context: server, identity, project."""

from __future__ import annotations

import os
from typing import Any, Optional

import typer

from aethis_cli._version import __version__
from aethis_cli.auth_helpers import resolve_cached_key
from aethis_cli.client import AethisClient
from aethis_cli.config import (
    DEFAULT_BASE_URL,
    active_profile_name,
    get_profile,
    load_project_config,
    read_state,
    resolve_base_url_with_source,
)
from aethis_cli.errors import AethisAPIError, ConfigError
from aethis_cli.output import console, error_panel
from aethis_cli.render import emit, is_json_requested


STATUS_HELP = """
Show the current CLI context: version, server, identity, and project.

Answers "what will the next command hit?" — useful for diagnosing missing
projects or rulesets (usually you're pointed at the wrong base URL).

With --project-id (or from a project directory), also shows generation
progress for that project.

Examples:

    aethis status
    aethis status -p proj_i1HyinBtFJniayUC
    aethis --base-url http://localhost:8080 status
    aethis status --output json | jq .identity.key_id
"""


def status(
    project_id: Optional[str] = typer.Option(
        None,
        "--project-id",
        "-p",
        help="Also show generation progress for this project (or from state.json).",
    ),
) -> None:
    """Show CLI context and optional project generation progress."""
    if is_json_requested():
        _emit_json_status(project_id)
        return

    _print_cli_section()
    _print_server_section()
    cfg, state_ruleset = _print_project_section()
    _print_identity_section()

    pid = project_id or (cfg.project_id if cfg else None)
    if pid:
        _print_generation_section(pid)
    elif state_ruleset:
        # Don't hit the API if we only have a ruleset_id and no project context
        pass


status.__doc__ = STATUS_HELP


def _print_cli_section() -> None:
    console.print(f"[bold]CLI:[/bold]         aethis v{__version__}")


def _print_server_section() -> None:
    base_url, source = resolve_base_url_with_source()
    if source == "default":
        console.print(f"[bold]Server:[/bold]      {base_url}")
    else:
        source_label = {
            "env": "from AETHIS_BASE_URL env var",
            "yaml": "from aethis.yaml",
            "profile": "from active profile",
        }.get(source, source)
        console.print(f"[bold]Server:[/bold]      [green]●[/green] {base_url}  [dim]({source_label})[/dim]")

    profile_name = active_profile_name()
    profile = get_profile(profile_name)
    auth_mode = profile.get("auth_mode") or "api_key"
    audience = profile.get("audience")
    aud_suffix = f"  [dim](audience: {audience})[/dim]" if audience else ""
    console.print(f"[bold]Profile:[/bold]     {profile_name}")
    console.print(f"[bold]Auth mode:[/bold]   {auth_mode}{aud_suffix}")


def _print_project_section() -> tuple[Optional[object], Optional[str]]:
    """Print project/config context. Returns (cfg, state_ruleset_id)."""
    try:
        cfg = load_project_config()
    except ConfigError:
        console.print("[bold]Project:[/bold]     [dim]no aethis.yaml in this directory[/dim]")
        return None, None

    console.print(f"[bold]Project:[/bold]     {cfg.project}")
    console.print(f"[bold]Config:[/bold]      {cfg.config_path / 'aethis.yaml'}")
    if cfg.project_id:
        console.print(f"[bold]Project ID:[/bold]  {cfg.project_id}")

    state = read_state(cfg.config_path)
    ruleset_id = state.get("ruleset_id")
    if ruleset_id:
        console.print(f"[bold]Ruleset:[/bold]      {ruleset_id}  [dim](from .aethis/state.json)[/dim]")
    return cfg, ruleset_id


def _print_identity_section() -> None:
    """Show identity from /me. Gracefully handle missing key or unreachable server."""
    base_url = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)
    try:
        cfg = load_project_config()
        base_url = cfg.base_url
    except ConfigError:
        pass

    profile = get_profile(active_profile_name())
    auth_mode = profile.get("auth_mode") or "api_key"
    if auth_mode != "api_key":
        # ``/me`` is an X-API-Key endpoint; non-api_key modes don't have a
        # tenant identity to show. The provider mints its credential at
        # request time, so "Identity" here just confirms the scheme.
        console.print(f"[bold]Identity:[/bold]    [dim]{auth_mode} (provider-minted at request time)[/dim]")
        return

    api_key = resolve_cached_key()
    if api_key is None:
        console.print(
            "[bold]Identity:[/bold]    [yellow]no API key[/yellow]  "
            "[dim](decision endpoints still work; run `aethis login` to author)[/dim]"
        )
        return

    client = AethisClient(api_key, base_url)
    try:
        me = client.whoami()
    except AethisAPIError as e:
        if e.status_code in (401, 403, 404):
            console.print(
                "[bold]Identity:[/bold]    [red]✗ API key rejected[/red]  "
                "[dim](run `aethis login` to re-authenticate)[/dim]"
            )
        else:
            console.print(
                f"[bold]Identity:[/bold]    [red]✗ could not reach /me (HTTP {e.status_code})[/red]  "
                f"[dim]({e.detail})[/dim]"
            )
        return

    key_id = me.get("key_id", "?")
    tenant = me.get("tenant_id", "?")
    tier = me.get("rate_limit_tier", "?")
    scopes = me.get("scopes") or []
    author_badge = "[green]✓ authoring[/green]" if me.get("can_author") else "[dim]read-only[/dim]"
    console.print(f"[bold]Identity:[/bold]    {key_id}  [dim]({tenant} · {tier} · {author_badge})[/dim]")
    if scopes:
        console.print(f"[bold]Scopes:[/bold]      {', '.join(sorted(scopes))}")


def _print_generation_section(project_id: str) -> None:
    """Print generation progress for a specific project."""
    try:
        cfg = load_project_config()
        base_url = cfg.base_url
    except ConfigError:
        base_url = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)

    api_key = resolve_cached_key()
    if api_key is None:
        console.print("[bold]Generation:[/bold]  [dim]skipped — no API key[/dim]")
        return

    client = AethisClient(api_key, base_url)
    try:
        result = client.get_status(project_id)
    except AethisAPIError as e:
        error_panel(e)
        return

    ps = result.get("project_status", "unknown")
    color = {"ready": "green", "failed": "red", "generating": "yellow"}.get(ps, "white")
    console.print(f"\n[bold]Generation[/bold] — {project_id}")
    console.print(f"  Project: [bold {color}]{ps}[/bold {color}]")
    job = result.get("job")
    if job:
        console.print(f"  Job:     {job.get('status')} ({job.get('progress_percent', 0)}%)")
        if job.get("error_message"):
            console.print(f"  Error:   [red]{job['error_message']}[/red]")
    bid = result.get("latest_ruleset_id")
    if bid:
        console.print(f"  Ruleset:  {bid}")


def _emit_json_status(project_id: Optional[str]) -> None:
    """Build and emit a structured status dict for --output json consumers.

    Scriptable view of the same context the human view shows. Keys mirror the
    section titles so `--json server,identity` gives a clean projection.
    """
    base_url, source = resolve_base_url_with_source()
    profile_name = active_profile_name()
    profile = get_profile(profile_name)

    state: dict[str, Any] = {
        "cli": {"version": __version__},
        "server": {"base_url": base_url, "source": source},
        "profile": {
            "name": profile_name,
            "auth_mode": profile.get("auth_mode") or "api_key",
            "audience": profile.get("audience"),
        },
    }

    # Project section
    cfg = None
    try:
        cfg = load_project_config()
        ruleset_id = read_state(cfg.config_path).get("ruleset_id")
        state["project"] = {
            "name": cfg.project,
            "config_path": str(cfg.config_path / "aethis.yaml"),
            "project_id": cfg.project_id,
            "ruleset_id": ruleset_id,
        }
    except ConfigError:
        state["project"] = None

    # Identity section
    auth_mode = profile.get("auth_mode") or "api_key"
    identity_base_url = cfg.base_url if cfg else base_url
    if auth_mode != "api_key":
        state["identity"] = {"auth_mode": auth_mode, "provider_minted": True}
    else:
        api_key = resolve_cached_key()
        if api_key is None:
            state["identity"] = {"key_present": False}
        else:
            client = AethisClient(api_key, identity_base_url)
            try:
                me = client.whoami()
                state["identity"] = {
                    "key_present": True,
                    "key_id": me.get("key_id"),
                    "tenant_id": me.get("tenant_id"),
                    "rate_limit_tier": me.get("rate_limit_tier"),
                    "scopes": me.get("scopes") or [],
                    "can_author": me.get("can_author"),
                }
            except AethisAPIError as e:
                state["identity"] = {
                    "key_present": True,
                    "error": str(e.detail),
                    "status_code": e.status_code,
                    "key_rejected": e.status_code in (401, 403, 404),
                }

    # Generation section — only when we have a project id
    pid = project_id or (cfg.project_id if cfg else None)
    if pid:
        api_key = resolve_cached_key()
        if api_key is None:
            state["generation"] = {"skipped": "no_api_key"}
        else:
            client = AethisClient(api_key, identity_base_url)
            try:
                state["generation"] = client.get_status(pid)
            except AethisAPIError as e:
                state["generation"] = {"error": str(e.detail), "status_code": e.status_code}

    emit(state)
