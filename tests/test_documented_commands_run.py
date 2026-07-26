"""Every command we print in a doc has to be a command that actually works.

The P8 review found the flagship README snippet — the shell-gate example
whose whole point is that a rejected input can never pass as success — was
`aethis decide … --output json`, which exits 2 with `No such option`. The
`if` branch then jq'd an empty file, so the *safety* example silently
misreported. `--output` is a root-callback option and must precede the
subcommand.

Two checks: the shape of every documented invocation, and a real execution of
the snippet the README leads with.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

REPO = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "contract"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_COMMAND_LINE = re.compile(r"^\s*(?:\$\s*)?(aethis\s+[^\n|>&#]+)")


def _documented_command_lines() -> list[tuple[str, str]]:
    sources = {"README.md": (REPO / "README.md").read_text()}
    for path in sorted((REPO / "aethis_cli" / "commands").glob("*.py")):
        sources[f"aethis_cli/commands/{path.name}"] = path.read_text()
    sources["aethis_cli/main.py"] = (REPO / "aethis_cli" / "main.py").read_text()

    found = []
    for origin, text in sources.items():
        for line in text.splitlines():
            match = _COMMAND_LINE.match(line)
            if match:
                found.append((origin, match.group(1).strip()))
    return found


def _option_map(command) -> dict:
    """{option string: takes a value} for one resolved command."""
    options = {}
    for param in command.params:
        for opt in getattr(param, "opts", []) + getattr(param, "secondary_opts", []):
            options[opt] = not getattr(param, "is_flag", False)
    return options


def _invalid_options(line: str) -> list[str]:
    """Walk a documented invocation the way Click parses it.

    Click binds root-callback options before the subcommand and subcommand
    options after it, so an option is valid only against whichever command is
    current at the point it appears. Resolving against the real command tree
    (rather than a list of "root-ish looking" flags) is what stops this check
    flagging `aethis review --json` or `aethis profile add --api-key`, which
    define those options themselves.
    """
    from typer.main import get_command

    from aethis_cli.main import app

    node = get_command(app)
    options = _option_map(node)
    invalid = []
    expecting_value = False

    for token in line.split()[1:]:  # drop "aethis"
        if expecting_value:
            expecting_value = False
            continue
        if token.startswith("-"):
            name, _, inline = token.partition("=")
            if name not in options:
                invalid.append(name)
            elif options[name] and not inline:
                expecting_value = True
            continue
        subcommands = getattr(node, "commands", None) or {}
        if token in subcommands:
            node = subcommands[token]
            # Past this point the root's own options are no longer bindable.
            options = _option_map(node)
    return invalid


def test_we_actually_found_documented_commands():
    """Guards the extractor: a regex matching nothing would make every
    assertion below vacuously true."""
    lines = _documented_command_lines()
    assert len(lines) > 20, f"only found {len(lines)} documented commands"
    assert any("decide" in line for _, line in lines)


def test_the_checker_catches_a_known_bad_invocation():
    """Guards the checker itself, with the exact defect the review found."""
    assert _invalid_options("aethis decide -b x --output json") == ["--output"]
    assert _invalid_options("aethis --output json decide -b x") == []
    # ...and does not flag options a subcommand genuinely defines.
    assert _invalid_options("aethis review --json") == []
    assert _invalid_options("aethis profile add admin --api-key ak_live_x") == []


def test_every_documented_command_is_a_valid_invocation():
    offenders = []
    for origin, line in _documented_command_lines():
        invalid = _invalid_options(line)
        if invalid:
            offenders.append(f"{origin}: {line}  ->  {', '.join(invalid)}")
    assert not offenders, "documented commands that would exit 2 with 'No such option':\n" + "\n".join(offenders)


def test_the_readme_shell_gate_snippet_runs_and_blocks(monkeypatch, tmp_path):
    """Execute the README's flagship example, in its documented flag order,
    against a blocked response: it must produce parseable JSON on stdout and
    exit 3, which is exactly what makes the `if`/`else` correct."""
    snippet = (REPO / "README.md").read_text()
    assert "aethis --output json decide -b <ruleset>" in snippet, "the documented form changed; update this test"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)

    client = MagicMock()
    client.decide.return_value = json.loads((FIXTURES / "decide_blocking_unknown_field.json").read_text())

    with patch("aethis_cli.client.AethisClient", return_value=client):
        from aethis_cli.main import app

        result = CliRunner().invoke(
            app,
            ["--output", "json", "decide", "-b", "aethis/spacecraft-crew-certification", "-i", '{"a":1}'],
            catch_exceptions=False,
        )

    assert result.exit_code == 3, result.output
    payload = json.loads(_ANSI_RE.sub("", result.output))
    assert payload["decision"] == "undetermined"
    assert payload["field_errors"]


@pytest.mark.parametrize("argv", [["decide", "--output", "json"], ["explain", "--output", "json"]])
def test_the_broken_flag_order_really_is_broken(argv, monkeypatch, tmp_path):
    """The negative half: confirms the shape check above is guarding against
    a real failure, not a style preference."""
    monkeypatch.chdir(tmp_path)
    from aethis_cli.main import app

    result = CliRunner().invoke(app, [*argv, "-b", "x:20260101-abcdef12"])
    assert result.exit_code == 2
    assert "No such option" in _ANSI_RE.sub("", result.output)
