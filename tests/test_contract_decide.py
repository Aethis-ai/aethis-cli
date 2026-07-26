"""The CLI never presents a blocked evaluation as a result.

Driven entirely off payloads captured from a live engine (see
`tests/fixtures/contract/README.md`). The property under test is the one a
developer's script depends on: when an input was rejected there is no
decision, so nothing may render as eligible or ineligible and nothing may
exit zero.
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

BLOCKING_FIXTURES = [
    "decide_blocking_unknown_field.json",
    "decide_blocking_bad_value.json",
    "decide_blocking_enum_value.json",
]


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def strip(text: str) -> str:
    return _ANSI_RE.sub("", text)


def run_decide(monkeypatch, tmp_path, response, extra_args=(), root_args=()):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)

    client = MagicMock()
    client.decide.return_value = response

    with patch("aethis_cli.client.AethisClient", return_value=client):
        from aethis_cli.main import app

        return CliRunner().invoke(
            app,
            [
                *root_args,
                "decide",
                "-b",
                "aethis/spacecraft-crew-certification",
                "-i",
                '{"space.crew.species":"Human"}',
                *extra_args,
            ],
            catch_exceptions=False,
        )


# ---------------------------------------------------------------------------
# The captured payloads say what the contract says
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", BLOCKING_FIXTURES)
def test_captured_blocking_payloads_are_undetermined(name):
    """Guards the fixtures themselves: a captured payload that had drifted to
    a terminal verdict would silently weaken every test below."""
    payload = load(name)
    assert payload["field_errors"], f"{name} should carry blocking field_errors"
    assert payload["decision"] == "undetermined"
    assert payload["ruleset_version"] not in (None, "", "unknown")
    assert payload["content_digest"].startswith("sha256:")


# ---------------------------------------------------------------------------
# Human output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", BLOCKING_FIXTURES)
def test_blocking_response_exits_non_zero(monkeypatch, tmp_path, name):
    result = run_decide(monkeypatch, tmp_path, load(name))
    assert result.exit_code == contract.EXIT_BLOCKING_INPUT, result.output


@pytest.mark.parametrize("name", BLOCKING_FIXTURES)
def test_blocking_response_never_presents_a_verdict(monkeypatch, tmp_path, name):
    result = run_decide(monkeypatch, tmp_path, load(name))
    out = strip(result.output)
    assert "eligible" not in out, out
    assert "Decision:" not in out, out
    assert "blocked" in out
    assert "Rejected inputs" in out


@pytest.mark.parametrize("name", BLOCKING_FIXTURES)
def test_blocking_response_names_every_rejected_field(monkeypatch, tmp_path, name):
    payload = load(name)
    result = run_decide(monkeypatch, tmp_path, payload)
    out = strip(result.output)
    for field_id in payload["field_errors"]:
        assert field_id in out, f"{field_id} missing from output"


def test_blocking_with_explanation_still_shows_the_logic_trace(monkeypatch, tmp_path):
    """The trace is diagnostic, so it stays -- but under its own heading, and
    never as a second verdict."""
    result = run_decide(
        monkeypatch,
        tmp_path,
        load("decide_blocking_enum_value.json"),
        extra_args=("--explain",),
    )
    out = strip(result.output)
    assert result.exit_code == contract.EXIT_BLOCKING_INPUT
    assert "Logic trace" in out
    assert "blocked" in out


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", BLOCKING_FIXTURES)
def test_blocking_json_exits_non_zero_and_records_the_block(monkeypatch, tmp_path, name):
    result = run_decide(monkeypatch, tmp_path, load(name), root_args=("--output", "json"))
    assert result.exit_code == contract.EXIT_BLOCKING_INPUT
    payload = json.loads(strip(result.output))
    assert payload["decision"] == "undetermined"
    note = payload[contract.CONTRACT_NOTE_KEY]
    assert note["presented_decision"] == "undetermined"
    assert note["exit_code"] == contract.EXIT_BLOCKING_INPUT
    assert sorted(load(name)["field_errors"]) == note["blocking_field_errors"]


def test_json_passes_a_conforming_response_through_unchanged(monkeypatch, tmp_path):
    payload = load("decide_terminal_eligible.json")
    result = run_decide(monkeypatch, tmp_path, payload, root_args=("--output", "json"))
    assert result.exit_code == 0
    assert json.loads(strip(result.output)) == payload
    assert contract.CONTRACT_NOTE_KEY not in strip(result.output)


# ---------------------------------------------------------------------------
# A server that contradicts the contract
# ---------------------------------------------------------------------------


def _contradicting_payload() -> dict:
    """A real blocking payload with a terminal verdict spliced in.

    Not a shape the current engine produces -- that is the point. A stale
    deployment, a caching proxy or a third-party API-compatible server can
    produce it, and the CLI must not become the component that turns it into
    a green exit status.
    """
    payload = load("decide_blocking_unknown_field.json")
    payload["decision"] = "eligible"
    payload["explanation"]["decision"] = "eligible"
    return payload


def test_contradicting_server_never_yields_a_positive_human_result(monkeypatch, tmp_path):
    result = run_decide(monkeypatch, tmp_path, _contradicting_payload(), extra_args=("--explain",))
    out = strip(result.output)
    assert result.exit_code == contract.EXIT_BLOCKING_INPUT
    assert "Decision: eligible" not in out
    assert "blocked" in out
    assert "Contract violation" in out


def test_contradicting_server_never_yields_a_positive_json_result(monkeypatch, tmp_path):
    result = run_decide(monkeypatch, tmp_path, _contradicting_payload(), root_args=("--output", "json"))
    assert result.exit_code == contract.EXIT_BLOCKING_INPUT
    payload = json.loads(strip(result.output))
    assert payload["decision"] == "undetermined"
    assert payload["explanation"]["decision"] == "undetermined"
    violations = payload[contract.CONTRACT_NOTE_KEY]["violations"]
    assert any("decision='eligible'" in v for v in violations)
    assert any("explanation.decision" in v for v in violations)


def test_guard_does_not_mutate_the_callers_response():
    payload = _contradicting_payload()
    before = json.dumps(payload, sort_keys=True)
    contract.guard_response(payload)
    assert json.dumps(payload, sort_keys=True) == before


# ---------------------------------------------------------------------------
# Undetermined-but-not-blocked is a normal, zero-exit answer
# ---------------------------------------------------------------------------


def test_incomplete_input_is_undetermined_and_exits_zero(monkeypatch, tmp_path):
    payload = load("decide_undetermined_incomplete.json")
    assert not payload["field_errors"]
    result = run_decide(monkeypatch, tmp_path, payload)
    out = strip(result.output)
    assert result.exit_code == 0, out
    assert "undetermined" in out
    assert "blocked" not in out
    assert "Next question" in out


@pytest.mark.parametrize(
    "name,expected",
    [
        ("decide_terminal_eligible.json", "eligible"),
        ("decide_terminal_not_eligible.json", "not_eligible"),
    ],
)
def test_terminal_results_render_and_exit_zero(monkeypatch, tmp_path, name, expected):
    result = run_decide(monkeypatch, tmp_path, load(name))
    out = strip(result.output)
    assert result.exit_code == 0, out
    assert f"Decision: {expected}" in out


# ---------------------------------------------------------------------------
# Malformed / corrupt responses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"decision": None, "field_errors": None},
        {"decision": "eligible", "field_errors": "everything is broken"},
        {"decision": "eligible", "field_errors": ["space.crew.age is wrong"]},
    ],
)
def test_malformed_response_never_crashes_and_never_overclaims(monkeypatch, tmp_path, payload):
    result = run_decide(monkeypatch, tmp_path, payload)
    out = strip(result.output)
    assert "Traceback" not in out
    if contract.is_blocked(payload):
        assert result.exit_code == contract.EXIT_BLOCKING_INPUT
        assert "Decision: eligible" not in out
