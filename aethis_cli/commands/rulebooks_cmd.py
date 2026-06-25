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
from rich.table import Table

from aethis_cli.auth_helpers import resolve_cached_key
from aethis_cli.client import make_anonymous_client
from aethis_cli.config import load_client_or_fallback, resolve_base_url_with_source
from aethis_cli.errors import AethisAPIError
from aethis_cli.output import console, error_panel, success
from aethis_cli.render import emit, is_json_requested

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


# Robot-hint "beats" the engine consumes today, plus the reserved beats it
# accepts but does not yet act on. Mirrors the aethis-core P1a contract
# (aethis-core#220). Unknown keys are rejected by the engine (422); we surface
# them client-side with a friendlier message before the round-trip.
_ACTIVE_ROBOT_HINT_BEATS = frozenset(
    {
        "general_context",
        "preamble",
        "session_start",
        "postamble",
        "session_end",
        "stuck",
    }
)
_RESERVED_ROBOT_HINT_BEATS = frozenset(
    {
        "persona",
        "conversational_style",
        "section_transition",
    }
)
_KNOWN_ROBOT_HINT_BEATS = _ACTIVE_ROBOT_HINT_BEATS | _RESERVED_ROBOT_HINT_BEATS


def _validate_robot_hints(raw: Any) -> dict[str, str]:
    """Validate a ``robot_hints:`` block: a mapping of beat-name → prose string.

    Returns the validated mapping. Raises ``typer.BadParameter`` on a bad shape,
    an unknown beat key, or a non-string value, so the CLI fails fast with a
    clear message rather than letting the engine 422 on a typo. Natural-language
    strings only — no DSL, no field keys.
    """
    if not isinstance(raw, dict):
        raise typer.BadParameter(f"robot_hints must be a mapping of beat-name to text; got {type(raw).__name__}.")
    hints: dict[str, str] = {}
    for beat, text in raw.items():
        if beat not in _KNOWN_ROBOT_HINT_BEATS:
            known = ", ".join(sorted(_KNOWN_ROBOT_HINT_BEATS))
            raise typer.BadParameter(f"robot_hints: unknown beat '{beat}'. Known beats: {known}.")
        if not isinstance(text, str):
            raise typer.BadParameter(
                f"robot_hints['{beat}'] must be a natural-language string; got {type(text).__name__}."
            )
        hints[beat] = text
    return hints


def _robot_hints_from_file(file: Optional[Path]) -> Optional[dict[str, str]]:
    """Read and validate a ``robot_hints:`` block from a rulebook YAML/JSON.

    Returns ``None`` when no file is given or the file has no ``robot_hints:``
    key, so the create/update payload omits the field entirely (clean no-op,
    unchanged behaviour). The block is a sibling of ``name``/``domain``/
    ``outcome_logic`` in a ``rulebook.yaml``.
    """
    if file is None:
        return None
    payload = _load_yaml_or_json(file)
    if not isinstance(payload, dict):
        raise typer.BadParameter(f"{file} must be a mapping (rulebook.yaml/.json), not a list or scalar.")
    if "robot_hints" not in payload or payload["robot_hints"] is None:
        return None
    return _validate_robot_hints(payload["robot_hints"])


# ============================================================================
# rulebooks list
# ============================================================================


def _build_rulebooks_table(rulebooks: list[dict], title: str = "Rulebooks") -> Table:
    table = Table(title=title)
    table.add_column("Slug", style="cyan")
    table.add_column("Rulebook ID", style="dim")
    table.add_column("Name")
    table.add_column("Domain")
    table.add_column("Status")
    table.add_column("Rulesets", justify="right")
    for rb in rulebooks:
        table.add_row(
            rb.get("slug") or "[dim]—[/dim]",
            rb.get("rulebook_id", ""),
            rb.get("name") or "[dim]—[/dim]",
            rb.get("domain") or "[dim]—[/dim]",
            rb.get("status", ""),
            str(len(rb.get("ruleset_refs", []) or [])),
        )
    return table


def _list_public_rulebooks() -> None:
    """Hit the anonymous rulebook catalogue and render the result.

    Mirrors the anonymous fallthrough on ``aethis rulesets list``. Requires
    aethis-core v0.29.0+ on the target API (live on api.aethis.ai).
    """
    base_url, _ = resolve_base_url_with_source()
    with make_anonymous_client(base_url) as client:
        try:
            rulebooks = client.list_public_rulebooks()
        except AethisAPIError as e:
            error_panel(e)
            raise typer.Exit(code=1)

    if not is_json_requested():
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


@rulebooks_app.command(name="list")
def list_rulebooks() -> None:
    """List rulebooks — your tenant's (with an API key) or the public catalogue.

    Example::

        aethis rulebooks list
    """
    # Rulebooks are tenant-scoped; with no key cached, fall through to the
    # anonymous cross-tenant public catalogue instead of dragging a
    # brand-new user through the browser sign-in for a read-only browse.
    if resolve_cached_key() is None:
        _list_public_rulebooks()
        return

    _cfg, client = load_client_or_fallback()
    try:
        rulebooks = client.list_rulebooks()
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    if not rulebooks:
        if is_json_requested():
            emit([])
        else:
            console.print("[dim]No rulebooks yet. Create one with `aethis rulebooks create`.[/dim]")
        return

    emit(rulebooks, table=lambda: _build_rulebooks_table(rulebooks))


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
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        exists=True,
        readable=True,
        help=(
            "Optional rulebook.yaml/.json to read a 'robot_hints:' block from "
            "(a mapping of beat-name to natural-language guidance for the "
            "assistant). Other rulebook keys in the file are ignored by this "
            "command; CLI flags own name/domain/slug/description."
        ),
    ),
) -> None:
    """Create a new Rulebook.

    The new rulebook is created with no rulesets, no field vocabulary, no
    tests, and ``status="draft"``. Populate it with::

        aethis rulebooks set-fields <id> -f fields.yaml
        aethis rulebooks tests add <id> -f scenario.yaml
        aethis rulesets create <rulebook> <ruleset_name>   # (Phase B.1b)

    Robot hints (assistant guidance) can be declared in a ``rulebook.yaml``
    and passed with ``--file``::

        robot_hints:
          preamble: "Greet the applicant and explain what you'll cover."
          stuck: "If an answer is unclear, ask one focused follow-up question."

    Active beats: general_context, preamble, session_start, postamble,
    session_end, stuck. Use natural language only — no rule syntax or field
    keys.
    """
    robot_hints = _robot_hints_from_file(file)

    _cfg, client = load_client_or_fallback()
    try:
        rb = client.create_rulebook(
            name=name,
            domain=domain,
            slug=slug,
            description=description,
            robot_hints=robot_hints,
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

    A wrapped ``rulebook.yaml`` shape is also accepted: when the top-level
    object carries an ``outcome_logic:`` key, that key is the Expr AST and a
    sibling ``robot_hints:`` block (assistant guidance, beat-name → prose) is
    pushed alongside it in the same update. This lets a single ``rulebook.yaml``
    declare both composition and robot hints::

        outcome_logic:
          type: field_ref
          key: single_ruleset_name
        robot_hints:
          preamble: "Greet the applicant and explain what you'll cover."
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

    # Wrapped rulebook.yaml form: `outcome_logic:` (and optional sibling
    # `robot_hints:`) at the top level. A bare Expr AST has a `type` key and no
    # `outcome_logic`, so it stays the default — backwards compatible.
    robot_hints: Optional[dict[str, str]] = None
    if "outcome_logic" in payload:
        if "robot_hints" in payload and payload["robot_hints"] is not None:
            robot_hints = _validate_robot_hints(payload["robot_hints"])
        outcome_logic = payload["outcome_logic"]
        if not isinstance(outcome_logic, dict):
            raise typer.BadParameter("outcome_logic must be a JSON object (Expr AST), not a list or scalar.")
    else:
        outcome_logic = payload

    _cfg, client = load_client_or_fallback()
    try:
        client.update_rulebook(rulebook, outcome_logic=outcome_logic, robot_hints=robot_hints)
    except AethisAPIError as e:
        error_panel(e)
        raise typer.Exit(code=1)

    op = outcome_logic.get("operator") or outcome_logic.get("type")
    msg = f"Set outcome_logic on rulebook {rulebook} (top-level: {op})"
    if robot_hints:
        msg += f" + {len(robot_hints)} robot hint(s)"
    success(msg)


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
