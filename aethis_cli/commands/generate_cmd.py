"""aethis generate — upload sources + guidance, trigger generation, poll until done."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from aethis_cli.client import AethisClient
from aethis_cli.config import load_project_config, resolve_anthropic_key, resolve_api_key, write_state
from aethis_cli.errors import AethisAPIError, ConfigError
from aethis_cli.output import console, error_panel, info, success


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def generate(
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p"),
    poll: bool = typer.Option(True, "--poll/--no-poll", help="Poll until generation completes"),
    timeout: int = typer.Option(600, "--timeout", "-t", help="Polling timeout in seconds"),
) -> None:
    """Upload sources + guidance, trigger bundle generation, and poll until done."""
    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
        anthropic_key = resolve_anthropic_key(cfg)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    client = AethisClient(api_key, cfg.base_url, anthropic_key=anthropic_key)
    project_dir = cfg.config_path

    try:
        # Resolve or create project
        pid = project_id or cfg.project_id
        if pid:
            # Verify the project still exists (may be stale from a different server)
            try:
                client.get_project(pid)
            except AethisAPIError as e:
                if e.status_code == 404:
                    info(f"Project {pid} not found on server, creating new project")
                    pid = None
                else:
                    raise
        if not pid:
            result = client.create_project(cfg.project, cfg.project, "")
            pid = result["project_id"]
            write_state(project_dir, {"project_id": pid})
            info(f"Created project {pid}")

        # Upload source files (batch in groups of 5)
        sources_dir = project_dir / "sources"
        if sources_dir.is_dir():
            resolved_root = sources_dir.resolve()
            source_files = sorted(
                f for f in sources_dir.rglob("*")
                if f.is_file() and f.resolve().is_relative_to(resolved_root)
            )
            if source_files:
                for batch in _chunks(source_files, 5):
                    client.upload_sources(pid, batch)
                info(f"Uploaded {len(source_files)} source(s)")

        # Upload guidance hints
        hints_path = project_dir / "guidance" / "hints.yaml"
        if hints_path.exists():
            if hints_path.stat().st_size > 1_000_000:
                console.print(f"[red]{hints_path} exceeds 1 MB limit[/red]")
                raise typer.Exit(code=1)
            try:
                raw = yaml.safe_load(hints_path.read_text()) or {}
            except yaml.YAMLError as e:
                console.print(f"[red]Invalid YAML in {hints_path}: {e}[/red]")
                raise typer.Exit(code=1)
            hints = raw.get("hints", [])
            count = 0
            for hint in hints:
                if not hint:
                    continue
                if isinstance(hint, str):
                    client.add_guidance(pid, hint)
                else:
                    text = hint.get("text", "")
                    if text:
                        process_type = hint.get("process_type", "rule_generation")
                        client.add_guidance(pid, text, process_type=process_type)
                count += 1
            if count:
                info(f"Added {count} guidance hint(s)")

        # Upload test cases
        tests_path = project_dir / "tests" / "scenarios.yaml"
        if tests_path.exists():
            if tests_path.stat().st_size > 1_000_000:
                console.print(f"[red]{tests_path} exceeds 1 MB limit[/red]")
                raise typer.Exit(code=1)
            try:
                raw = yaml.safe_load(tests_path.read_text()) or {}
            except yaml.YAMLError as e:
                console.print(f"[red]Invalid YAML in {tests_path}: {e}[/red]")
                raise typer.Exit(code=1)
            test_cases = raw.get("tests", [])
            if test_cases:
                normalised = [
                    {
                        "name": tc["name"],
                        "field_values": tc.get("inputs", {}),
                        "expected_outcome": tc.get("expect", {}).get("outcome", "eligible"),
                    }
                    for tc in test_cases
                ]
                client.add_tests(pid, normalised)
                info(f"Added {len(test_cases)} test case(s)")

        # Trigger generation
        job = client.generate(pid)
        write_state(project_dir, {"project_id": pid, "job_id": job["job_id"]})
        info(f"Generation queued (job={job['job_id']})")

        if not poll:
            console.print("Use 'aethis status' to check progress.")
            return

        # Poll with progress spinner
        _poll_until_done(client, pid, project_dir, timeout)

    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)


def _poll_until_done(client: AethisClient, pid: str, project_dir: Path, timeout: int = 600) -> None:
    deadline = time.monotonic() + timeout
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Generating bundle...", total=100)
        while time.monotonic() < deadline:
            result = client.get_status(pid)
            job = result.get("job") or {}
            pct = job.get("progress_percent", 0)
            job_status = job.get("status", "unknown")
            progress.update(task, completed=pct, description=f"[cyan]{job_status}[/cyan] — {pct}%")

            if job_status == "success":
                progress.update(task, completed=100)
                bundle_id = result.get("latest_bundle_id")
                write_state(project_dir, {"bundle_id": bundle_id})
                console.print()
                # Auto-publish so the bundle is immediately usable
                try:
                    client.publish(pid)
                    success(f"Done! Bundle published: {bundle_id}")
                except AethisAPIError:
                    success(f"Done! Bundle: {bundle_id} (run 'aethis publish' to activate)")
                return

            if job_status == "failed":
                console.print()
                console.print(f"[bold red]Generation failed:[/bold red] {job.get('error_message', 'unknown error')}")
                raise typer.Exit(code=1)

            time.sleep(3)

    console.print(f"\n[bold red]Timed out after {timeout}s.[/bold red] Use 'aethis status' to check progress.")
    raise typer.Exit(code=1)
