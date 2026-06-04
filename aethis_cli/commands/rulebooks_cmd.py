"""aethis rulebooks — author, configure, and evaluate Rulebooks.

In the converged 2-term authoring model (`docs/RULEBOOK_AUTHORING_MODEL.md`
in the workspace), a **Rulebook** is the whole form — the execution unit.
It owns the locked field vocabulary, the composition expression, rulebook-
level test cases, and an integer version history. Rulesets (the parts of a
rulebook) are managed via `aethis rulesets …` (separate command group).

This command group covers the rulebook lifecycle and configuration:

    aethis rulebooks list
    aethis rulebooks show <id-or-slug>
    aethis rulebooks create <name> --domain <d> [--slug ...]
    aethis rulebooks set-fields <id> -f fields.yaml
    aethis rulebooks lock-fields <id>
    aethis rulebooks unlock-fields <id>
    aethis rulebooks set-logic <id> -f logic.yaml
    aethis rulebooks tests add <id> -f scenario.yaml
    aethis rulebooks tests list <id>
    aethis rulebooks tests delete <id> <tc_id>
    aethis rulebooks activate <id>
    aethis rulebooks archive <id>
    aethis rulebooks decide <id> -i '{"field":"value"}'
    aethis rulebooks schema <id>
    aethis rulebooks explain <id>
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from aethis_cli.auth_helpers import resolve_cached_key
from aethis_cli.client import make_anonymous_client
from aethis_cli.config import load_client_or_fallback, resolve_base_url_with_source
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel, success
from aethis_cli.render import emit, is_json_requested

# Warnings must never land on stdout — `--json` consumers pipe it.
_stderr_console = Console(stderr=True)

rulebooks_app = typer.Typer(
    name="rulebooks",
    help="Author, configure, and evaluate Rulebooks.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

tests_app = typer.Typer(
    name="tests",
    help="Manage rulebook-level test cases.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _load_yaml_or_json(path: Path) -> Any:
    """Load a YAML or JSON file based on extension. YAML support is lazy
    so callers without PyYAML can still use JSON inputs."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise typer.BadParameter(
                f"YAML input requires PyYAML; install it or pass a .json file instead. ({exc})"
            ) from exc
        return yaml.safe_load(text)
    return json.loads(text)


# ============================================================================
# rulebooks list
# ============================================================================


def _build_rulebooks_table(
    rulebooks: list[dict], title: str = "Rulebooks", with_catalogue: bool = False
) -> Table:
    table = Table(title=title)
    table.add_column("Slug", style="cyan")
    table.add_column("Rulebook ID", style="dim")
    table.add_column("Name")
    table.add_column("Domain")
    table.add_column("Status")
    table.add_column("Rulesets", justify="right")
    if with_catalogue:
        table.add_column("Catalogue")
    for rb in rulebooks:
        row = [
            rb.get("slug") or "[dim]—[/dim]",
            rb.get("rulebook_id", ""),
            rb.get("name") or "[dim]—[/dim]",
            rb.get("domain") or "[dim]—[/dim]",
            rb.get("status", ""),
            str(len(rb.get("ruleset_refs", []) or [])),
        ]
        if with_catalogue:
            row.append("yours" if rb.get("catalogue") == "tenant" else "[dim]public[/dim]")
        table.add_row(*row)
    return table


def _list_public_rulebooks(explicit: bool = False) -> None:
    """Hit the anonymous rulebook catalogue and render the result.

    Mirrors the anonymous fallthrough on ``aethis rulesets list``. Requires
    aethis-core v0.29.0+ on the target API (live on api.aethis.ai).
    ``explicit`` suppresses the no-key hint when the user asked for the
    catalogue via ``--public`` (where "No API key" may be untrue).
    """
    base_url, _ = resolve_base_url_with_source()
    with make_anonymous_client(base_url) as client:
        try:
            rulebooks = client.list_public_rulebooks()
        except AethisAPIError as e:
            error_panel(e)
            raise typer.Exit(code=1)

    if not explicit and not is_json_requested():
        console.print("[dim]No API key — showing public rulebooks. Run `aethis login` to see yours.[/dim]")
    if not rulebooks:
        if is_json_requested():
            emit([])
        else:
            console.print(
                "[dim]No public rulebooks published yet. Browse public rulesets with `aethis rulesets list`.[/dim]"
            )
        return
    emit(rulebooks, table=lambda: _build_rulebooks_table(rulebooks, title="Public rulebooks"))


def _fetch_public_rulebooks() -> list[dict]:
    """Best-effort fetch of the anonymous public catalogue.

    Used to supplement an authenticated listing, so a catalogue outage must
    not take down `rulebooks list` — warn on stderr (never stdout, which may
    be JSON) and return an empty list instead of exiting.
    """
    base_url, _ = resolve_base_url_with_source()
    with make_anonymous_client(base_url) as client:
        try:
            return client.list_public_rulebooks()
        except AethisAPIError as e:
            _stderr_console.print(
                f"[yellow]![/yellow] Could not fetch the public catalogue: {e.detail} (HTTP {e.status_code})"
            )
            return []


@rulebooks_app.command(name="list")
def list_rulebooks(
    public: bool = typer.Option(
        False,
        "--public",
        help="List only the cross-tenant public catalogue (no auth required).",
    ),
) -> None:
    """List rulebooks — your tenant's plus the public catalogue.

    With an API key, shows your tenant's rulebooks and the cross-tenant
    public catalogue in one view (Catalogue column: yours / public). With
    no key — or with ``--public`` — shows only the public catalogue.

    Example::

        aethis rulebooks list
        aethis rulebooks list --public
    """
    # With no key cached, fall through to the anonymous cross-tenant public
    # catalogue instead of dragging a brand-new user through the browser
    # sign-in for a read-only browse.
    if public or resolve_cached_key() is None:
        _list_public_rulebooks(explicit=public)
        return

    _cfg, client = load_client_or_fallback()
    try:
        mine = client.list_rulebooks()
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    # Public rulebooks must stay visible to keyed users too — the catalogue
    # is part of the product surface, not an anonymous-only fallback.
    own_ids = {rb.get("rulebook_id") for rb in mine}
    public_rbs = [rb for rb in _fetch_public_rulebooks() if rb.get("rulebook_id") not in own_ids]

    rulebooks = [{**rb, "catalogue": "tenant"} for rb in mine] + [
        {**rb, "catalogue": "public"} for rb in public_rbs
    ]

    if not rulebooks:
        if is_json_requested():
            emit([])
        else:
            console.print("[dim]No rulebooks yet. Create one with `aethis rulebooks create`.[/dim]")
        return

    if not mine and not is_json_requested():
        console.print(
            "[dim]No rulebooks in your tenant yet — showing the public catalogue. "
            "Create one with `aethis rulebooks create`.[/dim]"
        )

    emit(rulebooks, table=lambda: _build_rulebooks_table(rulebooks, with_catalogue=True))


# ============================================================================
# rulebooks show
# ============================================================================


@rulebooks_app.command(name="show")
def show_rulebook(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
) -> None:
    """Show a single rulebook's full configuration."""
    _cfg, client = load_client_or_fallback()
    try:
        rb = client.get_rulebook(rulebook)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    emit(rb)


# ============================================================================
# rulebooks create
# ============================================================================


@rulebooks_app.command(name="create")
def create_rulebook(
    name: str = typer.Argument(..., help='Human-readable name (e.g. "UK FSM").'),
    domain: str = typer.Option(
        "",
        "--domain",
        "-d",
        help='Domain hint, lower-snake (e.g. "uk_fsm").',
    ),
    slug: Optional[str] = typer.Option(
        None,
        "--slug",
        help=(
            "Stable human-readable alias (e.g. aethis/uk-fsm). "
            "Globally unique when set; recommended for any rulebook you "
            "intend to reference from outside the CLI."
        ),
    ),
    description: Optional[str] = typer.Option(None, "--description", help="Optional description."),
) -> None:
    """Create a new Rulebook.

    The new rulebook is created with no rulesets, no field vocabulary, no
    tests, and ``status="draft"``. Populate it with::

        aethis rulebooks set-fields <id> -f fields.yaml
        aethis rulebooks tests add <id> -f scenario.yaml
        aethis rulesets create <rulebook> <ruleset_name>   # (Phase B.1b)
    """
    _cfg, client = load_client_or_fallback()
    try:
        rb = client.create_rulebook(
            name=name,
            domain=domain,
            slug=slug,
            description=description,
        )
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    success(f"Created rulebook {rb['rulebook_id']}" + (f" (slug: {rb['slug']})" if rb.get("slug") else ""))
    console.print_json(data=rb)


# ============================================================================
# rulebooks set-fields
# ============================================================================


@rulebooks_app.command(name="set-fields")
def set_fields(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        exists=True,
        readable=True,
        help="Path to fields.yaml or fields.json (list of {key, sort, ...}).",
    ),
) -> None:
    """Replace the rulebook's locked field vocabulary.

    Refused if currently locked — call ``unlock-fields`` first.

    The file is a list of field specs, e.g.::

        - key: applicant.age
          sort: Int
        - key: child.year_group
          sort: Enum
          enum_values: [reception, year_1, year_2]
    """
    payload = _load_yaml_or_json(file)
    if isinstance(payload, dict) and "fields" in payload:
        # Allow either a top-level list or a {fields: [...]} object.
        fields = payload["fields"]
    else:
        fields = payload
    if not isinstance(fields, list) or not fields:
        raise typer.BadParameter(f"{file} must contain a non-empty list of field specs.")

    _cfg, client = load_client_or_fallback()
    try:
        result = client.set_rulebook_fields(rulebook, fields)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    success(f"Set {len(result['fields'])} field(s) on rulebook {rulebook}")
    console.print(f"Lock state: [cyan]{result['field_lock_state']}[/cyan]")


# ============================================================================
# rulebooks lock-fields / unlock-fields / get-fields
# ============================================================================


@rulebooks_app.command(name="lock-fields")
def lock_fields(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
) -> None:
    """Lock the rulebook's field vocabulary.

    After locking, rulesets cannot introduce new field names. Call
    ``unlock-fields`` to make changes (will cut a new rulebook version
    in a later phase).
    """
    _cfg, client = load_client_or_fallback()
    try:
        result = client.lock_rulebook_fields(rulebook)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)
    success(f"Locked field vocabulary on rulebook {rulebook}")
    console.print(f"Locked field count: [cyan]{len(result.get('fields', []))}[/cyan]")


@rulebooks_app.command(name="unlock-fields")
def unlock_fields(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
) -> None:
    """Unlock the rulebook's field vocabulary so it can be modified."""
    _cfg, client = load_client_or_fallback()
    try:
        client.unlock_rulebook_fields(rulebook)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)
    success(f"Unlocked field vocabulary on rulebook {rulebook}")


@rulebooks_app.command(name="get-fields")
def get_fields(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
) -> None:
    """Print the rulebook's locked field vocabulary."""
    _cfg, client = load_client_or_fallback()
    try:
        result = client.get_rulebook_fields(rulebook)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    if is_json_requested():
        emit(result)
        return

    console.print(f"Lock state: [cyan]{result['field_lock_state']}[/cyan]")
    fields = result.get("fields", [])
    if not fields:
        console.print("[dim]No fields locked yet.[/dim]")
        return

    def _build_fields_table() -> Table:
        table = Table(title=f"Fields — {rulebook}")
        table.add_column("Key", style="cyan")
        table.add_column("Sort")
        table.add_column("Enum values")
        table.add_column("Description")
        for f in fields:
            table.add_row(
                f.get("key", ""),
                f.get("sort", ""),
                ", ".join(f.get("enum_values") or []) or "[dim]—[/dim]",
                f.get("description") or "[dim]—[/dim]",
            )
        return table

    emit(fields, table=_build_fields_table)


# ============================================================================
# rulebooks set-logic
# ============================================================================


@rulebooks_app.command(name="set-logic")
def set_logic(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        exists=True,
        readable=True,
        help="Path to a YAML or JSON file containing the outcome_logic Expr AST.",
    ),
    logic: Optional[str] = typer.Option(
        None,
        "--logic",
        "-l",
        help="Inline JSON Expr AST. Mutually exclusive with --file.",
    ),
) -> None:
    """Set the rulebook's composition expression (``outcome_logic``).

    The composition expression is an Expr AST that combines per-ruleset
    outcomes into the rulebook's final decision. The smallest example::

        {"type": "field_ref", "key": "single_ruleset_name"}

    A typical multi-ruleset composition, e.g. ``A AND (B OR C)``::

        {
          "type": "op", "operator": "and",
          "args": [
            {"type": "field_ref", "key": "A"},
            {"type": "op", "operator": "or", "args": [
              {"type": "field_ref", "key": "B"},
              {"type": "field_ref", "key": "C"}
            ]}
          ]
        }

    ``field_ref.key`` values are ruleset names within the rulebook (e.g.
    ``"child_eligibility"``); the engine resolves each name to the AND of
    that ruleset's compiled groups (requires aethis-core v0.26.0+). For
    advanced compositions you may also use an unscoped group name or the
    scoped ``<ruleset_name>.<group>`` form — both remain accepted for
    backwards compatibility. Pass the expression as either a file
    (``--file logic.yaml``) or inline JSON (``--logic '{...}'``). Exactly
    one of the two is required.
    """
    if (file is None) == (logic is None):
        # Either both supplied or both missing — neither is valid.
        raise typer.BadParameter("Provide exactly one of --file/-f or --logic/-l.")

    if file is not None:
        payload = _load_yaml_or_json(file)
    else:
        try:
            payload = json.loads(logic or "")
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"--logic is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise typer.BadParameter("outcome_logic must be a JSON object (Expr AST), not a list or scalar.")

    _cfg, client = load_client_or_fallback()
    try:
        client.update_rulebook(rulebook, outcome_logic=payload)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    op = payload.get("operator") or payload.get("type")
    success(f"Set outcome_logic on rulebook {rulebook} (top-level: {op})")


# ============================================================================
# rulebooks activate / archive
# ============================================================================


@rulebooks_app.command(name="activate")
def activate_rulebook(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
) -> None:
    """Mark a rulebook as active. Refused on archived rulebooks."""
    _cfg, client = load_client_or_fallback()
    try:
        result = client.activate_rulebook(rulebook)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)
    success(f"Activated rulebook {rulebook} (status: {result.get('status')})")


@rulebooks_app.command(name="archive")
def archive_rulebook(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Archive a rulebook (soft-delete; data preserved)."""
    if not yes:
        confirmed = typer.confirm(f"Archive rulebook {rulebook}? Cannot be undone")
        if not confirmed:
            raise typer.Abort()
    _cfg, client = load_client_or_fallback()
    try:
        result = client.archive_rulebook(rulebook)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)
    success(f"Archived rulebook {rulebook} (status: {result.get('status')})")


# ============================================================================
# rulebooks decide
# ============================================================================


@rulebooks_app.command(name="decide")
def decide_rulebook(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
    inputs: Optional[str] = typer.Option(
        None,
        "--inputs",
        "-i",
        help=("Field values as JSON inline (e.g. '{\"age\": 21}') or @path.json to load from disk."),
    ),
    input_file: Optional[Path] = typer.Option(
        None,
        "--input-file",
        exists=True,
        readable=True,
        help="YAML or JSON file with field values (alternative to --inputs).",
    ),
    explain: bool = typer.Option(False, "--explain", help="Include a human-readable explanation."),
) -> None:
    """Evaluate a rulebook against a set of field values.

    Examples::

        aethis rulebooks decide aethis/uk-fsm -i '{"applicant.age": 6}'
        aethis rulebooks decide aethis/uk-fsm --input-file persona.yaml --explain
    """
    if inputs is None and input_file is None:
        raise typer.BadParameter("Provide field values via --inputs / -i or --input-file.")
    if inputs and input_file:
        raise typer.BadParameter("Pass either --inputs or --input-file, not both.")

    if input_file is not None:
        field_values = _load_yaml_or_json(input_file)
    else:
        # `inputs` may be inline JSON or @path syntax.
        if inputs and inputs.startswith("@"):
            field_values = _load_yaml_or_json(Path(inputs[1:]))
        else:
            try:
                field_values = json.loads(inputs or "{}")
            except json.JSONDecodeError as exc:
                raise typer.BadParameter(f"--inputs must be valid JSON: {exc}") from exc

    if not isinstance(field_values, dict):
        raise typer.BadParameter("Field values must be a JSON object / YAML mapping.")

    _cfg, client = load_client_or_fallback()
    try:
        opts: dict[str, Any] = {}
        if explain:
            opts["include_explanation"] = True
        result = client.decide_rulebook(rulebook, field_values, **opts)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    emit(result)


# ============================================================================
# rulebooks schema / explain
# ============================================================================


@rulebooks_app.command(name="schema")
def schema_rulebook(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
) -> None:
    """Print the combined field schema across all live rulesets."""
    _cfg, client = load_client_or_fallback()
    try:
        result = client.get_rulebook_schema(rulebook)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)
    emit(result)


@rulebooks_app.command(name="explain")
def explain_rulebook(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
) -> None:
    """Print human-readable rule explanations for the rulebook."""
    _cfg, client = load_client_or_fallback()
    try:
        result = client.explain_rulebook(rulebook)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)
    emit(result)


# ============================================================================
# rulebooks tests {add,list,delete}
# ============================================================================


@tests_app.command(name="add")
def tests_add(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        exists=True,
        readable=True,
        help=(
            "YAML or JSON file with {name, field_values, expected_outcome}. "
            "May also be a list to add multiple test cases at once."
        ),
    ),
) -> None:
    """Add one or more rulebook-level (full-form) test cases.

    A test case carries::

        name: "Reception, low income"
        field_values:
          applicant.age: 5
          household.income: 8000
        expected_outcome: eligible   # or not_eligible / undetermined
    """
    payload = _load_yaml_or_json(file)
    cases: list[dict[str, Any]]
    if isinstance(payload, dict):
        cases = [payload]
    elif isinstance(payload, list):
        cases = payload
    else:
        raise typer.BadParameter(f"{file} must contain a test-case object or a list of them.")

    _cfg, client = load_client_or_fallback()
    added: list[dict[str, Any]] = []
    for tc in cases:
        if not isinstance(tc, dict):
            raise typer.BadParameter(f"Each test case must be a mapping; got {type(tc).__name__}.")
        try:
            result = client.add_rulebook_test_case(
                rulebook,
                name=tc["name"],
                field_values=tc["field_values"],
                expected_outcome=tc["expected_outcome"],
            )
        except KeyError as exc:
            raise typer.BadParameter(f"Test case missing required key: {exc}") from exc
        except AethisAPIError as e:
            error_panel(e)
            raise typer.Exit(code=1)
        added.append(result)

    success(f"Added {len(added)} test case(s) to rulebook {rulebook}")
    for tc in added:
        console.print(f"  {tc['tc_id']}  {tc['name']}  → {tc['expected_outcome']}")


@tests_app.command(name="list")
def tests_list(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
) -> None:
    """List rulebook-level test cases."""
    _cfg, client = load_client_or_fallback()
    try:
        result = client.list_rulebook_test_cases(rulebook)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)
    cases = result.get("test_cases", [])
    if not cases:
        if is_json_requested():
            emit([])
        else:
            console.print("[dim]No test cases yet.[/dim]")
        return

    def _build_tests_table() -> Table:
        table = Table(title=f"Rulebook test cases — {rulebook}")
        table.add_column("tc_id", style="cyan")
        table.add_column("Name")
        table.add_column("Expected outcome")
        table.add_column("Fields", justify="right")
        for tc in cases:
            table.add_row(
                tc.get("tc_id", ""),
                tc.get("name", ""),
                tc.get("expected_outcome", ""),
                str(len(tc.get("field_values", {}) or {})),
            )
        return table

    emit(cases, table=_build_tests_table)


@tests_app.command(name="delete")
def tests_delete(
    rulebook: str = typer.Argument(..., help="Rulebook ID or slug."),
    tc_id: str = typer.Argument(..., help="Test case ID (tc_*)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete a rulebook-level test case by tc_id."""
    if not yes:
        confirmed = typer.confirm(f"Delete test case {tc_id} from rulebook {rulebook}?")
        if not confirmed:
            raise typer.Abort()
    _cfg, client = load_client_or_fallback()
    try:
        client.delete_rulebook_test_case(rulebook, tc_id)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)
    success(f"Deleted test case {tc_id} from rulebook {rulebook}")


rulebooks_app.add_typer(tests_app, name="tests")
