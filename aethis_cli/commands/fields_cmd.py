"""aethis fields — inspect and manage a project's field definitions.

Bare ``aethis fields`` shows the field schema for a ruleset (no API key needed
for public rulesets). The subcommands manage the local ``fields/fields.yaml``:

- ``aethis fields discover`` — seed/merge LLM-proposed fields from the sources.
- ``aethis fields pull``     — sync the server's produced fields back to disk.
- ``aethis fields validate`` — check the local file before a generate.
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from aethis_cli.commands.generate_cmd import (
    _field_layer_errors,
    _is_collection_or_computed,
    _load_yaml_file,
    _parse_fields_yaml,
    _resolve_or_create_project,
    _safe_field_type,
    _upload_sources,
    _write_fields_yaml,
)
from aethis_cli.config import (
    load_client_or_anon,
    load_project_config,
    make_authed_client,
    read_state,
    resolve_anthropic_key,
    resolve_api_key,
)
from aethis_cli.errors import AethisAPIError, ConfigError
from aethis_cli.output import console, error_panel, success
from aethis_cli.render import emit, is_json_requested

fields_app = typer.Typer(
    help="Inspect a ruleset's fields and manage the local fields/fields.yaml vocabulary.",
    no_args_is_help=False,
)


def _is_collection_or_computed_row(row: dict) -> bool:
    """Whether a server field row is a collection (any case) or a computed field (``computed_from``)."""
    return str(row.get("field_type") or "").strip().lower() == "collection" or bool(row.get("computed_from"))


def _refuse_unauthored_collections(server_keys: list[str], field_map: dict, verb: str) -> None:
    """Exit before any write if the server holds a collection or computed field fields.yaml does not declare.

    The engine publishes a collection's ``items`` and a computed field's
    ``computed_from`` but never the full ``{op, collection, values}``
    declaration, so these cannot be written faithfully from server data. A key
    already declared locally as one is left exactly as authored and is not
    counted here.
    """
    unsupported = [k for k in server_keys if not _is_collection_or_computed(field_map.get(k, {}))]
    if not unsupported:
        return
    console.print(
        f"[red]Cannot {verb}: the server has collection or computed field(s) fields.yaml does not declare: "
        f"{', '.join(unsupported)}.[/red]"
    )
    console.print(
        "[red]These declarations are not published in full, so they are not supported here and nothing was "
        "written. They must be authored in fields.yaml (type: collection with items, and computed on a bool), "
        "then run again.[/red]"
    )
    raise typer.Exit(code=1)


def _show_fields(ruleset_id: Optional[str]) -> None:
    """Render the field schema for a ruleset (the bare ``aethis fields`` behaviour)."""
    cfg, client = load_client_or_anon()

    if not ruleset_id:
        state = read_state(cfg.config_path)
        ruleset_id = state.get("ruleset_id")
        if not ruleset_id:
            console.print("[red]No ruleset_id. Run 'aethis generate' first or pass --ruleset-id.[/red]")
            raise typer.Exit(code=1)

    try:
        result = client.get_schema(ruleset_id)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    field_specs = result.get("fields", [])

    if is_json_requested():
        emit(field_specs)
        return

    def _build_fields_table() -> Table:
        table = Table(title=f"Fields — {ruleset_id}")
        table.add_column("Field ID", style="cyan")
        table.add_column("Type")
        table.add_column("Description")
        table.add_column("Enum values")
        for f in field_specs:
            enum_vals = ", ".join(f["enum_values"]) if f.get("enum_values") else ""
            table.add_row(f["field_id"], f["field_type"], f.get("description") or "", enum_vals)
        return table

    emit(field_specs, table=_build_fields_table)


@fields_app.callback(invoke_without_command=True)
def fields(
    ctx: typer.Context,
    ruleset_id: Optional[str] = typer.Option(
        None,
        "--ruleset-id",
        "-b",
        help=(
            "Ruleset ID or slug (e.g. aethis/consumer-credit-prequalification or "
            "my_ruleset:20260408-abc1234). Defaults to .aethis/state.json if omitted."
        ),
    ),
) -> None:
    """Show the input fields expected by a ruleset. No API key required for public rulesets.

    Examples:

        aethis fields -b aethis/spacecraft-crew-certification
        aethis fields -b crew_certification:20260408-cbf63f1f
        aethis fields                    # uses .aethis/state.json if present
        aethis fields discover           # propose fields from the sources
        aethis fields pull               # sync server fields back to fields.yaml
    """
    if ctx.invoked_subcommand is not None:
        return
    _show_fields(ruleset_id)


def _ensure_project_and_sources(client, cfg) -> str:
    """Resolve (or create) the project and upload local sources so discovery has
    something to read. Returns the project id."""
    pid = _resolve_or_create_project(client, cfg)
    _upload_sources(client, pid, cfg.config_path)
    return pid


@fields_app.command("discover")
def discover() -> None:
    """Discover candidate fields from the project's sources and merge them into fields/fields.yaml.

    Uploads the project's local ``sources/`` first (creating the project if
    needed), then runs server-side LLM field discovery. Existing entries in
    ``fields.yaml`` are preserved — only genuinely new keys are appended — so
    your hand-authored labels/questions/hints are never clobbered.

    Requires an LLM key (set ``ANTHROPIC_API_KEY``), same as ``aethis generate``.
    """
    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
        anthropic_key = resolve_anthropic_key(cfg)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    client = make_authed_client(api_key, cfg.base_url, anthropic_key=anthropic_key)
    fields_path = cfg.config_path / "fields" / "fields.yaml"
    field_map = _parse_fields_yaml(fields_path) if fields_path.exists() else {}

    try:
        pid = _ensure_project_and_sources(client, cfg)
        result = client.discover_fields(pid)
    except AethisAPIError as e:
        # The server rejects discovery without an LLM key (unless the API key is
        # internal). Surface the actionable fix instead of the raw header error.
        if e.status_code == 400 and "anthropic" in e.detail.lower():
            console.print(
                f"[red]Field discovery needs an LLM key.[/red] Set [bold]{cfg.anthropic_key_env}[/bold] "
                "to your Anthropic API key and re-run (e.g. `export ANTHROPIC_API_KEY=sk-ant-...`)."
            )
            raise typer.Exit(code=1)
        error_panel(e)
        raise typer.Exit(code=1)

    discovered = result.get("fields", []) or []
    _refuse_unauthored_collections(
        [df["key"] for df in discovered if df.get("key") and _is_collection_or_computed_row(df)],
        field_map,
        "discover",
    )
    added = 0
    for df in discovered:
        key = df.get("key")
        if not key or key in field_map:
            continue
        ftype = _safe_field_type(df.get("field_type"), df.get("enum_values"))
        entry: dict = {"key": key, "type": ftype}
        question = df.get("question") or df.get("description")
        if question:
            entry["question"] = question
        if ftype == "enum" and df.get("enum_values"):
            entry["enum_values"] = df["enum_values"]
        description = df.get("description")
        if description and description != entry.get("question"):
            entry["hints"] = [description]
        field_map[key] = entry
        added += 1

    _write_fields_yaml(fields_path, field_map)
    success(f"Discovered {len(discovered)} field(s): added {added} new, kept {len(field_map) - added} existing.")
    score = result.get("completeness_score")
    if score is not None:
        console.print(f"Completeness: {score:.0%}")
    for gap in result.get("critical_gaps", []) or []:
        console.print(f"  [yellow]gap:[/yellow] {gap}")
    recommendation = result.get("recommendation")
    if recommendation:
        console.print(f"[dim]{recommendation}[/dim]")
    console.print(f"[dim]Wrote {fields_path}[/dim]")


@fields_app.command("pull")
def pull(
    ruleset_id: Optional[str] = typer.Option(
        None,
        "--ruleset-id",
        "-b",
        help="Ruleset ID or slug to pull from. Defaults to .aethis/state.json's ruleset_id.",
    ),
) -> None:
    """Sync the server's authoritative field definitions back into fields/fields.yaml.

    The server is authoritative for each field's key, type and enum values
    (post-generation reality). Local-only annotations — ``label`` and ``hints``
    — are preserved. Fields present locally but absent from the server schema
    are kept and reported, so you can decide whether they're still wanted.
    """
    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    rid = ruleset_id or read_state(cfg.config_path).get("ruleset_id")
    if not rid:
        console.print("[red]No ruleset_id. Run 'aethis generate' first or pass --ruleset-id.[/red]")
        raise typer.Exit(code=1)

    client = make_authed_client(api_key, cfg.base_url)
    try:
        result = client.get_schema(rid)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    server_fields = result.get("fields", []) or []
    fields_path = cfg.config_path / "fields" / "fields.yaml"
    field_map = _parse_fields_yaml(fields_path) if fields_path.exists() else {}

    _refuse_unauthored_collections(
        [sf["field_id"] for sf in server_fields if sf.get("field_id") and _is_collection_or_computed_row(sf)],
        field_map,
        "pull",
    )

    server_keys = set()
    added = updated = 0
    for sf in server_fields:
        key = sf.get("field_id")
        if not key:
            continue
        server_keys.add(key)
        if _is_collection_or_computed(field_map.get(key, {})):
            # Declared locally as a collection or computed field: left exactly
            # as authored. The server's rows for it are not the full declaration.
            continue
        entry = dict(field_map.get(key, {}))  # preserve local label/hints
        existed = key in field_map
        entry["key"] = key
        entry["type"] = _safe_field_type(sf.get("field_type"), sf.get("enum_values"))
        if entry.get("value_space"):
            # A field pinned to a named value space keeps the reference and
            # NEVER materialises members locally — writing the server's
            # expanded enum_values back would silently un-migrate the field
            # to inline form on first pull (aethis-core#424, C3).
            entry.pop("enum_values", None)
        elif entry["type"] == "enum" and sf.get("enum_values"):
            entry["enum_values"] = sf["enum_values"]
        else:
            entry.pop("enum_values", None)
        if sf.get("question"):
            entry["question"] = sf["question"]
        field_map[key] = entry
        updated += existed
        added += not existed

    _write_fields_yaml(fields_path, field_map)
    success(f"Pulled {len(server_fields)} field(s): {added} added, {updated} updated.")

    local_only = sorted(set(field_map) - server_keys)
    if local_only:
        console.print(f"[yellow]Kept local-only (not in server schema):[/yellow] {', '.join(local_only)}")
    console.print(f"[dim]Wrote {fields_path}[/dim]")


@fields_app.command("validate")
def validate() -> None:
    """Validate fields/fields.yaml: known types, no duplicate keys, enum needs enum_values."""
    try:
        cfg = load_project_config()
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    fields_path = cfg.config_path / "fields" / "fields.yaml"
    if not fields_path.exists():
        console.print(f"[red]{fields_path} not found. Run 'aethis init' or 'aethis fields discover' first.[/red]")
        raise typer.Exit(code=1)

    # Validate the raw lists (not the de-duplicated map) so duplicate keys
    # surface, and the enclosing rulebook's rows against this ruleset's keys.
    for path, errors in _field_layer_errors(cfg.config_path)[:1]:
        console.print(f"[red]{path} is invalid:[/red]")
        for e in errors:
            console.print(f"  [red]✗[/red] {e}")
        raise typer.Exit(code=1)

    raw_fields = _load_yaml_file(fields_path).get("fields") or []
    success(f"fields.yaml is valid ({len(raw_fields)} field(s)).")
