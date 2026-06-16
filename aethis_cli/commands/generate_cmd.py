"""aethis generate — upload sources + guidance, trigger generation, poll until done."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from aethis_cli.client import AethisClient
from aethis_cli.config import (
    load_project_config,
    make_authed_client,
    resolve_anthropic_key,
    resolve_api_key,
    write_state,
)
from aethis_cli.errors import AethisAPIError, ConfigError
from aethis_cli.output import console, error_panel, info, success


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _load_yaml_file(path: Path) -> dict:
    """Read + parse a project YAML file, failing fast on oversize / bad YAML."""
    if path.stat().st_size > 1_000_000:
        console.print(f"[red]{path} exceeds 1 MB limit[/red]")
        raise typer.Exit(code=1)
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        console.print(f"[red]Invalid YAML in {path}: {e}[/red]")
        raise typer.Exit(code=1)


def _parent_rulebook_dir(project_dir: Path) -> Optional[Path]:
    """Return the enclosing rulebook directory if this project is a member ruleset.

    Matches the scaffold shape ``<rulebook>/rulesets/<ruleset>/`` so rulebook-level
    guidance + fields can be propagated down into the ruleset's generation.
    """
    parent = project_dir.parent
    if parent.name == "rulesets" and (parent.parent / "aethis.yaml").exists():
        return parent.parent
    return None


def _parse_fields_yaml(path: Path) -> dict:
    """Parse a fields.yaml into an ordered ``{key: field_dict}`` map."""
    raw = _load_yaml_file(path)
    out: dict = {}
    for f in raw.get("fields", []) or []:
        if isinstance(f, dict) and f.get("key"):
            out[f["key"]] = f
    return out


def _field_guidance_lines(key: str, field: dict) -> list[str]:
    """Natural-language guidance derived from a field's label/question/hints.

    The ``/fields/spec`` endpoint only fixes key + type, so the human-facing
    phrasing and the "why we ask" notes ride along as guidance instead.
    """
    lines: list[str] = []
    if field.get("question"):
        lines.append(f'Ask field "{key}" using this question: {field["question"]}')
    if field.get("label"):
        lines.append(f'Label field "{key}" as: {field["label"]}')
    for hint in field.get("hints", []) or []:
        if hint:
            lines.append(f'Field "{key}": {hint}')
    return lines


def _upload_field_vocabulary(client: AethisClient, pid: str, project_dir: Path) -> None:
    """Push the field vocabulary for this project (rulebook-level fields win).

    Merges the enclosing rulebook's ``fields/fields.yaml`` (if any) with the
    project's own — the rulebook definition wins on shared keys — then pins the
    expected field keys/types via ``/fields/spec`` and routes each field's
    label/question/hints through guidance so a shared field is defined once.
    """
    field_map: dict = {}

    rb_dir = _parent_rulebook_dir(project_dir)
    if rb_dir is not None:
        rb_fields = rb_dir / "fields" / "fields.yaml"
        if rb_fields.exists():
            field_map.update(_parse_fields_yaml(rb_fields))

    own_fields = project_dir / "fields" / "fields.yaml"
    if own_fields.exists():
        for key, field in _parse_fields_yaml(own_fields).items():
            field_map.setdefault(key, field)  # rulebook wins: don't overwrite

    if not field_map:
        return

    expected_fields: list[dict] = []
    guidance_lines: list[str] = []
    for key, field in field_map.items():
        spec = {"key": key, "sort": field.get("type") or field.get("sort")}
        if field.get("enum_values"):
            spec["enum_values"] = field["enum_values"]
        expected_fields.append(spec)
        guidance_lines.extend(_field_guidance_lines(key, field))

    client.set_field_spec(pid, expected_fields)
    for line in guidance_lines:
        client.add_guidance(pid, line)
    info(f"Set field spec ({len(expected_fields)} field(s))")


def _upload_rulebook_guidance(client: AethisClient, pid: str, project_dir: Path) -> None:
    """Propagate the enclosing rulebook's guidance hints into this ruleset."""
    rb_dir = _parent_rulebook_dir(project_dir)
    if rb_dir is None:
        return
    rb_hints = rb_dir / "guidance" / "hints.yaml"
    if not rb_hints.exists():
        return
    raw = _load_yaml_file(rb_hints)
    count = 0
    for hint in raw.get("hints", []) or []:
        if not hint:
            continue
        text = hint if isinstance(hint, str) else hint.get("text", "")
        if text:
            client.add_guidance(pid, text)
            count += 1
    if count:
        info(f"Propagated {count} rulebook guidance hint(s)")


def generate(
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p"),
    poll: bool = typer.Option(True, "--poll/--no-poll", help="Poll until generation completes"),
    timeout: int = typer.Option(600, "--timeout", "-t", help="Polling timeout in seconds"),
    mode: str = typer.Option(
        "fresh",
        "--mode",
        help="fresh = author from scratch; refine = minimal edit seeded from the active ruleset",
    ),
    seed_ruleset_id: Optional[str] = typer.Option(
        None,
        "--seed-ruleset-id",
        help="Ruleset to seed a refine from (defaults to the section's active ruleset)",
    ),
) -> None:
    """Upload sources + guidance, trigger ruleset generation, and poll until done."""
    _run_generate(
        project_id=project_id,
        poll=poll,
        timeout=timeout,
        mode=mode,
        seed_ruleset_id=seed_ruleset_id,
    )


def _run_generate(
    *,
    project_id: Optional[str],
    poll: bool,
    timeout: int,
    mode: str = "fresh",
    seed_ruleset_id: Optional[str] = None,
    extra_hint: Optional[str] = None,
) -> None:
    """Shared machinery for ``aethis generate`` and ``aethis refine``."""
    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
        anthropic_key = resolve_anthropic_key(cfg)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    client = make_authed_client(api_key, cfg.base_url, anthropic_key=anthropic_key)
    project_dir = cfg.config_path

    # Fail-fast on empty sources: generation without any source documents wastes
    # 60-120s on the server and produces a cryptic LLM failure.
    sources_dir = project_dir / "sources"
    if sources_dir.is_dir():
        _resolved = sources_dir.resolve()
        _source_files = [f for f in sources_dir.rglob("*") if f.is_file() and f.resolve().is_relative_to(_resolved)]
    else:
        _source_files = []
    if not _source_files:
        console.print(
            f"[red]No source documents found in {sources_dir}.[/red]\n"
            "[dim]Add at least one source file (.md, .txt, .pdf) before running 'aethis generate'.[/dim]"
        )
        raise typer.Exit(code=1)

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

        # Refinement hint (aethis refine --hint): add before regenerating so it
        # informs the minimal edit.
        if extra_hint:
            client.add_guidance(pid, extra_hint)
            info("Added refinement hint")

        # Upload source files (batch in groups of 5)
        sources_dir = project_dir / "sources"
        if sources_dir.is_dir():
            resolved_root = sources_dir.resolve()
            source_files = sorted(
                f for f in sources_dir.rglob("*") if f.is_file() and f.resolve().is_relative_to(resolved_root)
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

        # Propagate rulebook-level guidance + push the field vocabulary. A field
        # (e.g. date of birth) defined once at the rulebook level flows down here
        # so the end user is only asked for it once. Rulebook fields win.
        _upload_rulebook_guidance(client, pid, project_dir)
        _upload_field_vocabulary(client, pid, project_dir)

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
        if mode == "refine":
            info("Refining: seeding from the active ruleset and making the minimal edit to fix failing tests")
        job = client.generate(pid, mode=mode, seed_ruleset_id=seed_ruleset_id)
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
        task = progress.add_task("Generating ruleset...", total=100)
        while time.monotonic() < deadline:
            result = client.get_status(pid)
            job = result.get("job") or {}
            pct = job.get("progress_percent", 0)
            job_status = job.get("status", "unknown")
            progress.update(task, completed=pct, description=f"[cyan]{job_status}[/cyan] — {pct}%")

            if job_status == "success":
                progress.update(task, completed=100)
                ruleset_id = result.get("latest_ruleset_id")
                write_state(project_dir, {"ruleset_id": ruleset_id})
                console.print()
                # Auto-publish so the ruleset is immediately usable
                try:
                    client.publish(pid)
                    success(f"Done! Ruleset published: {ruleset_id}")
                except AethisAPIError:
                    success(f"Done! Ruleset: {ruleset_id} (run 'aethis publish' to activate)")
                return

            if job_status == "failed":
                console.print()
                console.print(f"[bold red]Generation failed:[/bold red] {job.get('error_message', 'unknown error')}")
                raise typer.Exit(code=1)

            time.sleep(3)

    console.print(f"\n[bold red]Timed out after {timeout}s.[/bold red] Use 'aethis status' to check progress.")
    raise typer.Exit(code=1)
