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


def test_blocking_exit_code_is_three():
    """Pinned as a literal, deliberately. Every other exit assertion in this
    file also compares against the literal 3 -- asserting against the
    constant would be a tautology that survives redefining it."""
    assert contract.EXIT_BLOCKING_INPUT == 3
    assert contract.EXIT_OK == 0
    assert contract.EXIT_ERROR == 1


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
    assert result.exit_code == 3, result.output


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
    assert result.exit_code == 3
    assert "Logic trace" in out
    assert "blocked" in out


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", BLOCKING_FIXTURES)
def test_blocking_json_exits_non_zero_and_records_the_block(monkeypatch, tmp_path, name):
    result = run_decide(monkeypatch, tmp_path, load(name), root_args=("--output", "json"))
    assert result.exit_code == 3
    payload = json.loads(strip(result.output))
    assert payload["decision"] == "undetermined"
    note = payload[contract.CONTRACT_NOTE_KEY]
    assert note["presented_decision"] == "undetermined"
    assert note["exit_code"] == 3
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
    """A real blocking payload with a terminal verdict spliced into every
    place the engine's own forcing sweep scrubs.

    Not a shape the current engine produces -- that is the point. A stale
    deployment, a caching proxy or a third-party API-compatible server can
    produce it, and the CLI must not become the component that turns it into
    a green exit status. Poisoning all five sites (not just the two the first
    version of this helper touched) is what keeps the scrub code from being
    dead weight the suite never exercises.
    """
    payload = load("decide_blocking_unknown_field.json")
    payload["decision"] = "eligible"
    payload["explanation"]["decision"] = "eligible"
    payload["explanation"]["decision_path"] = "CERTIFIED_VIA_FLIGHT_HOURS"
    payload["trace"] = {
        "status": "eligible",
        "path": "CERTIFIED_VIA_FLIGHT_HOURS",
        "satisfied_requirement": "CERTIFIED_VIA_FLIGHT_HOURS",
        "group_statuses": {"flight_readiness": "satisfied"},
    }
    return payload


@pytest.mark.parametrize(
    "site,poison,read_back",
    [
        ("decision", lambda p: p.__setitem__("decision", "eligible"), lambda p: p["decision"]),
        (
            "explanation.decision",
            lambda p: p["explanation"].__setitem__("decision", "eligible"),
            lambda p: p["explanation"]["decision"],
        ),
        (
            "explanation.decision_path",
            lambda p: p["explanation"].__setitem__("decision_path", "SOME_PATH"),
            lambda p: p["explanation"].get("decision_path"),
        ),
        (
            "trace.status",
            lambda p: p.__setitem__("trace", {"status": "eligible"}),
            lambda p: p["trace"]["status"],
        ),
        (
            "trace.path",
            lambda p: p.__setitem__("trace", {"status": "undetermined", "path": "SOME_PATH"}),
            lambda p: p["trace"].get("path"),
        ),
    ],
)
def test_every_embedded_verdict_copy_is_scrubbed(site, poison, read_back):
    """One test per scrub site, so removing any single override turns this
    file red. The engine scrubs all five when a response is blocked; a CLI
    scrubbing a subset renders `Satisfied by: X` under a blocked result."""
    payload = load("decide_blocking_unknown_field.json")
    if not isinstance(payload.get("explanation"), dict):
        payload["explanation"] = {"groups": []}
    poison(payload)
    guarded = contract.guard_response(payload)
    assert read_back(guarded) in (None, "undetermined"), f"{site} survived the guard"
    assert any(site.split(".")[-1] in v for v in guarded[contract.CONTRACT_NOTE_KEY]["violations"])


def test_blocked_output_never_claims_a_satisfying_path(monkeypatch, tmp_path):
    """The reviewer's finding: a blocked run with --explain printed
    `Satisfied by: CERTIFIED_VIA_FLIGHT_HOURS` three times under a
    `Result: blocked` heading."""
    result = run_decide(monkeypatch, tmp_path, _contradicting_payload(), extra_args=("--explain",))
    out = strip(result.output)
    assert result.exit_code == 3
    assert "Satisfied by" not in out
    # The path may still be *named* in the violation report -- that is the
    # CLI saying what it dropped. What it must never do is present it.
    claims = [line for line in out.splitlines() if "Contract violation" not in line]
    assert not any("CERTIFIED_VIA_FLIGHT_HOURS" in line for line in claims), claims
    assert any("decision_path" in line and "Contract violation" in line for line in out.splitlines())


def test_contradicting_server_never_yields_a_positive_human_result(monkeypatch, tmp_path):
    result = run_decide(monkeypatch, tmp_path, _contradicting_payload(), extra_args=("--explain",))
    out = strip(result.output)
    assert result.exit_code == 3
    assert "Decision: eligible" not in out
    assert "blocked" in out
    assert "Contract violation" in out


def test_contradicting_server_never_yields_a_positive_json_result(monkeypatch, tmp_path):
    result = run_decide(monkeypatch, tmp_path, _contradicting_payload(), root_args=("--output", "json"))
    assert result.exit_code == 3
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
    "payload,expected_exit",
    [
        ({}, 0),
        ({"decision": None, "field_errors": None}, 0),
        ({"decision": "eligible", "field_errors": {}}, 0),
        # Non-mapping `field_errors`: a contract-breaking server, but errors
        # are errors -- they block, whatever container they arrive in.
        ({"decision": "eligible", "field_errors": "everything is broken"}, 3),
        ({"decision": "eligible", "field_errors": ["space.crew.age is wrong"]}, 3),
        ({"decision": "eligible", "field_errors": {"space.crew.age": "bad"}}, 3),
    ],
)
def test_malformed_response_never_crashes_and_never_overclaims(monkeypatch, tmp_path, payload, expected_exit):
    """Every case asserts an exact exit code.

    The earlier version guarded its assertions behind `if
    contract.is_blocked(payload)`, so breaking that predicate made the test
    vacuous instead of red -- it was reading the very function it was meant
    to be checking.
    """
    result = run_decide(monkeypatch, tmp_path, payload)
    out = strip(result.output)
    assert "Traceback" not in out
    assert result.exit_code == expected_exit, out
    if expected_exit == 3:
        assert "Decision: eligible" not in out
        assert "blocked" in out


@pytest.mark.parametrize(
    "field_errors,expected_keys",
    [
        ({"a": "bad"}, ["a"]),
        (["first", "second"], ["[0]", "[1]"]),
        ("everything is broken", ["__field_errors__"]),
    ],
)
def test_every_field_errors_container_shape_blocks(field_errors, expected_keys):
    """A server returning `field_errors` as a list or a bare string is
    broken, but it is still reporting rejected inputs -- reading only the
    mapping shape would treat those as a clean response."""
    payload = {"decision": "eligible", "field_errors": field_errors}
    assert contract.is_blocked(payload) is True
    assert sorted(contract.blocking_field_errors(payload)) == sorted(expected_keys)
    assert contract.presented_decision(payload) == "undetermined"
    assert contract.guard_response(payload)["decision"] == "undetermined"


def test_presented_decision_reads_the_error_channel():
    """`presented_decision` must consult `field_errors`, not echo `decision`
    -- it is the single function every renderer trusts."""
    assert contract.presented_decision({"decision": "eligible", "field_errors": {"a": "b"}}) == "undetermined"
    assert contract.presented_decision({"decision": "eligible"}) == "eligible"
    assert contract.presented_decision({"decision": "not_eligible", "field_errors": {}}) == "not_eligible"
