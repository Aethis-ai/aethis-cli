"""aethis init — first-run wizard: optional login, scaffold, next-step ladder."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import typer

from aethis_cli.config import ProjectConfig, resolve_api_key, write_state
from aethis_cli.errors import ConfigError
from aethis_cli.output import console, info, success

AETHIS_YAML_TEMPLATE = """\
project: {name}
kind: {kind}
api_key_env: AETHIS_API_KEY
# rulebook: ../..   # (rulesets only) path to the enclosing rulebook; declares
#                   # membership explicitly. Defaults to directory position
#                   # (<rulebook>/rulesets/<this>/) when omitted.
# base_url: https://api.aethis.ai
"""

HINTS_YAML_TEMPLATE = """\
hints:
  # - "Add your guidance hints here"
"""

# Rulebook-level guidance carries a note that it reaches every member ruleset.
RULEBOOK_HINTS_YAML_TEMPLATE = """\
hints:
  # Rulebook-level guidance — applied to every ruleset under this rulebook.
  # - "Add shared guidance hints here"
"""

# The field vocabulary lives in its own file (not in tests/ or guidance/) so a
# field is defined once and reused. `type` is the field's value type; `hints`
# are plain-language notes that explain why a field is asked.
FIELDS_YAML_TEMPLATE = """\
# fields/fields.yaml — the field vocabulary for this {scope}.
# Define each field once here so an end user is only asked for it once.
fields:
  # - key: applicant.date_of_birth
  #   type: date
  #   label: "Date of birth"
  #   question: "What is your date of birth?"
  #   hints:
  #     - "Why we ask: age determines which route applies."
"""

SCENARIOS_YAML_TEMPLATE = """\
tests:
  # - name: "eligible case"
  #   inputs: {{field_key: value}}
  #   expect: {{outcome: eligible}}
"""

GITIGNORE = """\
.aethis/
"""

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def _has_cached_auth() -> bool:
    """Return True if an API key is resolvable from env / keychain / file."""
    try:
        # We only need to know if a key exists; the values in ProjectConfig
        # other than api_key_env do not affect lookup.
        resolve_api_key(ProjectConfig(project=""))
        return True
    except ConfigError:
        return False


def _ensure_logged_in(no_prompt: bool) -> None:
    """If no auth is cached, run the login flow (unless --no-prompt)."""
    if _has_cached_auth():
        return
    if no_prompt:
        console.print(
            "[red]No API key found and --no-prompt is set. Run 'aethis login' first or set $AETHIS_API_KEY.[/red]"
        )
        raise typer.Exit(code=1)

    info("No API key cached — running login first.")
    # Imported lazily so test patches (`aethis_cli.commands.login_cmd.login`) work.
    from aethis_cli.commands import login_cmd

    login_cmd.login()


def _scaffold_ruleset(proj: Path, name: str) -> None:
    """Lay down a ruleset project: sources + guidance + fields + tests."""
    (proj / "aethis.yaml").write_text(AETHIS_YAML_TEMPLATE.format(name=name, kind="ruleset"))
    (proj / "sources").mkdir()
    (proj / "guidance").mkdir()
    (proj / "guidance" / "hints.yaml").write_text(HINTS_YAML_TEMPLATE)
    (proj / "fields").mkdir()
    (proj / "fields" / "fields.yaml").write_text(FIELDS_YAML_TEMPLATE.format(scope="ruleset"))
    (proj / "tests").mkdir()
    (proj / "tests" / "scenarios.yaml").write_text(SCENARIOS_YAML_TEMPLATE)
    (proj / ".gitignore").write_text(GITIGNORE)


def _scaffold_rulebook(proj: Path, name: str) -> None:
    """Lay down a rulebook: shared guidance + field vocabulary + rulesets/.

    A rulebook composes rulesets. Its `guidance/` and `fields/` are defined once
    here and propagate down to every member ruleset (rulebook wins). Sources
    belong to the individual rulesets, so a rulebook has no `sources/`.
    """
    (proj / "aethis.yaml").write_text(AETHIS_YAML_TEMPLATE.format(name=name, kind="rulebook"))
    (proj / "guidance").mkdir()
    (proj / "guidance" / "hints.yaml").write_text(RULEBOOK_HINTS_YAML_TEMPLATE)
    (proj / "fields").mkdir()
    (proj / "fields" / "fields.yaml").write_text(FIELDS_YAML_TEMPLATE.format(scope="rulebook"))
    (proj / "tests").mkdir()
    (proj / "tests" / "scenarios.yaml").write_text(SCENARIOS_YAML_TEMPLATE)
    # rulesets/ holds the member ruleset directories (scaffold each with
    # `aethis init <name> --kind ruleset` from inside it).
    (proj / "rulesets").mkdir()
    (proj / ".gitignore").write_text(GITIGNORE)


def _print_next_steps(name: str, kind: str) -> None:
    """Emit the canonical next-step ladder shown after init."""
    success(f"Project initialised: {name} ({kind})")
    console.print()
    console.print("Next:")
    console.print(f"  cd {name}")
    if kind == "rulebook":
        console.print("  # edit fields/fields.yaml and guidance/hints.yaml (shared by all rulesets)")
        console.print("  cd rulesets && aethis init <ruleset-name> --kind ruleset")
    else:
        console.print("  # edit fields/fields.yaml to declare your field vocabulary")
        console.print("  aethis generate --poll")
        console.print("  aethis test && aethis publish")


def init(
    name: Optional[str] = typer.Argument(
        None,
        help="Project name (will prompt if omitted, defaults to current directory name).",
    ),
    kind: str = typer.Option(
        "ruleset",
        "--kind",
        help="What to scaffold: 'ruleset' (a single rule set) or 'rulebook' (composes rulesets, with shared guidance + fields).",
    ),
    no_prompt: bool = typer.Option(
        False,
        "--no-prompt",
        help="Fail rather than prompt for missing values; skip the login flow.",
    ),
) -> None:
    """Scaffold a new Aethis project — runs login first if no auth is cached.

    Examples:

        aethis init                    # interactive wizard (login if needed, prompt for name)
        aethis init my-policy          # positional name, prompts only if no auth cached
        aethis init my-policy --no-prompt    # scripted use; errors if anything is missing
        aethis init my-rulebook --kind rulebook   # scaffold a rulebook (shared fields + guidance)
    """
    # 0. Validate kind up front.
    kind = (kind or "").strip().lower()
    if kind not in ("ruleset", "rulebook"):
        console.print("[red]--kind must be 'ruleset' or 'rulebook'.[/red]")
        raise typer.Exit(code=1)

    # 1. Resolve project name (prompt if missing, unless --no-prompt).
    if name is None:
        if no_prompt:
            console.print(
                "[red]A project name is required when --no-prompt is set (pass it as a positional argument).[/red]"
            )
            raise typer.Exit(code=1)
        default_name = Path.cwd().name
        name = typer.prompt("Project name", default=default_name)

    name = (name or "").strip()
    if not _NAME_RE.match(name):
        console.print("[red]Project name must be alphanumeric (with . _ - allowed, no path separators).[/red]")
        raise typer.Exit(code=1)

    # 2. Auth check before we touch the filesystem so the user isn't left with
    #    a half-scaffolded directory if they Ctrl-C the browser flow.
    _ensure_logged_in(no_prompt=no_prompt)

    # 3. Scaffold.
    proj = Path(name)
    if proj.exists():
        console.print(f"[red]Directory '{name}' already exists.[/red]")
        raise typer.Exit(code=1)

    proj.mkdir()
    if kind == "rulebook":
        _scaffold_rulebook(proj, name)
    else:
        _scaffold_ruleset(proj, name)

    # Pre-create .aethis/state.json so downstream commands can assume it
    # exists. The project_id stays unset until `aethis generate` actually
    # creates the project on the server (see generate_cmd).
    write_state(proj, {})

    # 4. Print the next-step ladder. Always shown (prompted or not).
    _print_next_steps(name, kind)
