"""aethis init — scaffold a new project directory."""

from __future__ import annotations

from pathlib import Path

import typer

from aethis_cli.output import console, success

AETHIS_YAML_TEMPLATE = """\
project: {name}
api_key_env: AETHIS_API_KEY
# base_url: https://api.aethis.ai
"""

HINTS_YAML_TEMPLATE = """\
hints: []
  # - "Add your guidance hints here"
"""

SCENARIOS_YAML_TEMPLATE = """\
tests: []
  # - name: "eligible case"
  #   inputs: {{field_key: value}}
  #   expect: {{outcome: eligible}}
"""

GITIGNORE = """\
.aethis/
"""


def init(name: str = typer.Argument(..., help="Project name")) -> None:
    """Scaffold a new Aethis project directory."""
    proj = Path(name)
    if proj.exists():
        console.print(f"[red]Directory '{name}' already exists.[/red]")
        raise typer.Exit(code=1)

    proj.mkdir()
    (proj / "aethis.yaml").write_text(AETHIS_YAML_TEMPLATE.format(name=name))
    (proj / "sources").mkdir()
    (proj / "guidance").mkdir()
    (proj / "guidance" / "hints.yaml").write_text(HINTS_YAML_TEMPLATE)
    (proj / "tests").mkdir()
    (proj / "tests" / "scenarios.yaml").write_text(SCENARIOS_YAML_TEMPLATE)
    (proj / ".gitignore").write_text(GITIGNORE)

    success(f"Created project '{name}/'")
    console.print("  aethis.yaml")
    console.print("  sources/")
    console.print("  guidance/hints.yaml")
    console.print("  tests/scenarios.yaml")
    console.print(f"\nNext: cd {name} && aethis generate")
