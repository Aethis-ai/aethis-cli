"""aethis cancel — explicitly release an in-flight generation."""

from __future__ import annotations

from typing import Optional

import typer

from aethis_cli.config import load_client_or_fallback, load_project_config
from aethis_cli.errors import AethisAPIError, ConfigError
from aethis_cli.output import console, error_panel, success, warn
from aethis_cli.prompts import confirm_or_abort, is_noninteractive
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
        help="Project whose observed in-flight generation should be cancelled (defaults to aethis.yaml).",
    ),
    job_id: Optional[str] = typer.Option(
        None,
        "--job-id",
        help=(
            "Expected generation job. Refuses if the project's current job differs; "
            "the exact cancelled job may be supplied to replay an ambiguous prior request."
        ),
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Mark the current job failed and release its project ownership.

    Worker shutdown may be cooperative rather than immediate. This recovery
    action is never invoked automatically by ``generate`` or ``status``.
    """
    pid = _resolve_project_id(project_id)
    _cfg, client = load_client_or_fallback()
    try:
        status = client.get_status(pid)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)
    if status.get("generation_contract_version") != 1:
        console.print(
            "[red]Cancellation is unavailable: this engine has not advertised the job-bound recovery contract.[/red]"
        )
        raise typer.Exit(code=1)
    current_job = status.get("job") if isinstance(status, dict) else None
    observed_job_id = current_job.get("job_id") if isinstance(current_job, dict) else None
    observed_status = current_job.get("status") if isinstance(current_job, dict) else None
    error_detail = current_job.get("error_detail") if isinstance(current_job, dict) else None
    already_cancelled = (
        observed_status == "failed"
        and isinstance(error_detail, dict)
        and error_detail.get("reason_code") == "generation_cancelled"
    )
    if not isinstance(observed_job_id, str):
        console.print("[red]No in-flight generation job was observed for this project.[/red]")
        raise typer.Exit(code=1)
    if job_id is not None and job_id != observed_job_id:
        console.print(
            f"[red]Refusing: expected job {job_id}, but the project currently reports {observed_job_id}.[/red]"
        )
        raise typer.Exit(code=1)
    if observed_status not in ("queued", "running") and not (job_id == observed_job_id and already_cancelled):
        console.print(
            "[red]No cancellable generation was observed. To retry an ambiguous prior cancellation, "
            "pass the exact terminal --job-id; only generation_cancelled is replayable.[/red]"
        )
        raise typer.Exit(code=1)
    if not yes and is_noninteractive():
        console.print("[red]Cancellation in a non-interactive environment requires explicit --yes.[/red]")
        raise typer.Exit(code=1)
    confirm_or_abort(
        f"Cancel generation {observed_job_id} for {pid}? This marks that job failed and releases its ownership",
        assume_yes=yes,
    )

    try:
        result = client.cancel_generation(pid, observed_job_id)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    if is_json_requested():
        emit(result)
        return

    job_id = result.get("job_id", "unknown")
    outcome = result.get("outcome")
    if result.get("project_released") is True:
        if outcome in ("cancelled", "already_cancelled"):
            success(f"Generation {job_id} {outcome.replace('_', ' ')}; project {pid} released.")
        else:
            warn(f"Generation {job_id} returned an unknown cancellation outcome; project {pid} released.")
    else:
        warn(f"Generation {job_id} was marked failed, but project {pid} was not released by this request.")
    detail = result.get("detail")
    if detail:
        console.print(str(detail), style="yellow", markup=False, highlight=False)
