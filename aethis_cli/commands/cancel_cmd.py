"""aethis cancel — explicitly release an in-flight generation."""

from __future__ import annotations

from typing import Optional

import typer

from aethis_cli.config import load_client_or_fallback, load_project_config
from aethis_cli.errors import AethisAPIError, ConfigError
from aethis_cli.output import console, error_panel, success, warn
from aethis_cli.prompts import confirm_or_abort
from aethis_cli.render import emit, is_json_requested


def _resolve_project_id(project_id: Optional[str]) -> str:
    if project_id:
        return project_id
    try:
        cfg = load_project_config()
    except ConfigError:
        console.print("[red]No project ID. Run from an Aethis project or pass --project-id.[/red]")
        raise typer.Exit(code=1)
    if not cfg.project_id:
        console.print("[red]This project has no project_id yet. Pass --project-id.[/red]")
        raise typer.Exit(code=1)
    return cfg.project_id


def cancel(
    project_id: Optional[str] = typer.Option(
        None,
        "--project-id",
        "-p",
        help="Project whose latest in-flight generation should be cancelled (defaults to aethis.yaml).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Mark the current job failed and release its project ownership.

    Worker shutdown may be cooperative rather than immediate. This recovery
    action is never invoked automatically by ``generate`` or ``status``.
    """
    pid = _resolve_project_id(project_id)
    confirm_or_abort(
        f"Cancel the in-flight generation for {pid}? This marks the job failed and releases the project",
        assume_yes=yes,
    )

    _cfg, client = load_client_or_fallback()
    try:
        result = client.cancel_generation(pid)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    if is_json_requested():
        emit(result)
        return

    job_id = result.get("job_id", "unknown")
    if result.get("project_released") is True:
        success(f"Generation {job_id} cancelled; project {pid} released.")
    else:
        warn(f"Generation {job_id} was marked failed, but project {pid} was not released by this request.")
    detail = result.get("detail")
    if detail:
        console.print(str(detail), style="yellow", markup=False, highlight=False)
