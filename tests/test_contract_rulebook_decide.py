"""`aethis rulebooks decide` is a decide surface and obeys the decide contract.

This file exists because the first version of the safety work guarded
`aethis decide` and left `aethis rulebooks decide` a bare `emit(result)`: the
documented exit-code contract simply did not exist on that surface, and the
README's shell-gate recipe would have passed a blocked evaluation straight
through. Same contract, same guard, same exit code — proven here on its own
fixtures so the two surfaces cannot drift apart again.

The rulebook envelope is serialised by the engine's own `DecideResponse`
model (no public rulebook is published on staging to capture from); see
`tests/fixtures/contract/README.md`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from aethis_cli import contract

FIXTURES = Path(__file__).parent / "fixtures" / "contract"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

RULEBOOK = "aethis/spacecraft-certification-book"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def strip(text: str) -> str:
    return _ANSI_RE.sub("", text)


def run_rulebook_decide(monkeypatch, tmp_path, response, extra=(), root=()):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)

    client = MagicMock()
    client.decide_rulebook.return_value = response

    with patch("aethis_cli.client.AethisClient", return_value=client):
        from aethis_cli.main import app

        return CliRunner().invoke(
            app,
            [*root, "rulebooks", "decide", RULEBOOK, "-i", '{"space.crew.species":"Human"}', *extra],
            catch_exceptions=False,
        )


def test_the_fixture_is_a_blocked_rulebook_decision():
    payload = load("decide_rulebook_blocking.json")
    assert payload["rulebook_id"]
    assert payload["ruleset_id"] is None
    assert payload["field_errors"]
    assert payload["decision"] == "undetermined"


def test_blocked_rulebook_decide_exits_three(monkeypatch, tmp_path):
    result = run_rulebook_decide(monkeypatch, tmp_path, load("decide_rulebook_blocking.json"))
    assert result.exit_code == 3, result.output


def test_blocked_rulebook_decide_names_the_rejected_input(monkeypatch, tmp_path):
    payload = load("decide_rulebook_blocking.json")
    out = strip(run_rulebook_decide(monkeypatch, tmp_path, payload).output)
    assert "blocked" in out
    for field_id in payload["field_errors"]:
        assert field_id in out


def test_blocked_rulebook_decide_json_is_guarded(monkeypatch, tmp_path):
    result = run_rulebook_decide(
        monkeypatch,
        tmp_path,
        load("decide_rulebook_blocking.json"),
        root=("--output", "json"),
    )
    assert result.exit_code == 3
    payload = json.loads(strip(result.output))
    assert payload["decision"] == "undetermined"
    assert payload[contract.CONTRACT_NOTE_KEY]["exit_code"] == 3


@pytest.mark.parametrize("root", [(), ("--output", "json")])
def test_contradicting_rulebook_server_is_overridden(monkeypatch, tmp_path, root):
    """The exact failure the review reproduced against the built wheel:
    `decision: eligible` beside non-empty `field_errors`, in human *and*
    JSON, exit 0, no contract note."""
    payload = {**load("decide_rulebook_blocking.json"), "decision": "eligible"}
    result = run_rulebook_decide(monkeypatch, tmp_path, payload, root=root)
    out = strip(result.output)
    assert result.exit_code == 3, out
    assert '"decision": "eligible"' not in out
    assert "Decision: eligible" not in out
    if root:
        assert json.loads(out)[contract.CONTRACT_NOTE_KEY]["violations"]


def test_unblocked_rulebook_decide_still_exits_zero(monkeypatch, tmp_path):
    payload = {**load("decide_rulebook_blocking.json"), "field_errors": None, "decision": "eligible"}
    result = run_rulebook_decide(monkeypatch, tmp_path, payload)
    assert result.exit_code == 0, result.output


def test_rulebook_unknown_version_is_not_a_violation():
    """`ruleset_version: "unknown"` is legal for a rulebook composite —
    resolved composition identity is a separate piece of engine work — so it
    must not be reported as a contract violation the way it is for a leaf."""
    payload = load("decide_rulebook_blocking.json")
    assert payload["ruleset_version"] == "unknown"
    assert not any("ruleset_version" in v for v in contract.contract_violations(payload))
    ident = contract.resolved_identity(payload)
    assert ident.rulebook_id
    assert "ruleset_version" not in ident.unresolved


def test_every_decide_surface_is_guarded():
    """A structural check, so a third decide surface cannot be added without
    the guard. Both commands must route their response through the contract
    before any output."""
    sources = {
        "decide": Path("aethis_cli/commands/decide_cmd.py").read_text(),
        "rulebooks decide": Path("aethis_cli/commands/rulebooks_cmd.py").read_text(),
    }
    for surface, text in sources.items():
        assert "contract.guard_response" in text, f"{surface} does not guard its response"
        assert "EXIT_BLOCKING_INPUT" in text, f"{surface} does not use the blocking exit code"
