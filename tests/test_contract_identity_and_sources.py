"""Immutable identity and supporting sources survive to the developer.

A decision is only worth anything later if you can say which rule artefact
produced it. That identity -- immutable ruleset id, published version,
content digest -- has to reach both the human reading a terminal and the
script parsing JSON, and the citations behind the rules have to be
distinguishable from the rules themselves.

Every payload is captured; see `tests/fixtures/contract/README.md`.
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

RULESET = "aethis/spacecraft-crew-certification"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def strip(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _invoke(monkeypatch, tmp_path, client, argv):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "220")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    with patch("aethis_cli.client.AethisClient", return_value=client):
        from aethis_cli.main import app

        return CliRunner().invoke(app, argv, catch_exceptions=False)


def run_decide(monkeypatch, tmp_path, response, extra=(), root=()):
    client = MagicMock()
    client.decide.return_value = response
    return _invoke(
        monkeypatch,
        tmp_path,
        client,
        [*root, "decide", "-b", RULESET, "-i", '{"space.crew.species":"Human"}', *extra],
    )


def run_explain(monkeypatch, tmp_path, response, root=()):
    client = MagicMock()
    client.explain.return_value = response
    return _invoke(monkeypatch, tmp_path, client, [*root, "explain", "-b", RULESET])


# ---------------------------------------------------------------------------
# Identity: decide
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "decide_terminal_eligible.json",
        "decide_terminal_not_eligible.json",
        "decide_undetermined_incomplete.json",
        "decide_blocking_unknown_field.json",
    ],
)
def test_decide_human_output_carries_immutable_identity(monkeypatch, tmp_path, name):
    payload = load(name)
    out = strip(run_decide(monkeypatch, tmp_path, payload).output)
    assert payload["ruleset_id"] in out
    assert payload["ruleset_version"] in out
    assert payload["content_digest"] in out
    assert payload["engine_version"] in out


@pytest.mark.parametrize(
    "name",
    ["decide_terminal_eligible.json", "decide_blocking_bad_value.json"],
)
def test_decide_json_output_carries_immutable_identity(monkeypatch, tmp_path, name):
    payload = load(name)
    emitted = json.loads(strip(run_decide(monkeypatch, tmp_path, payload, root=("--output", "json")).output))
    for key in ("ruleset_id", "ruleset_version", "content_digest", "inputs_hash", "engine_version"):
        assert emitted[key] == payload[key]


def test_unresolved_version_is_called_out_not_printed_as_identity(monkeypatch, tmp_path):
    """`unknown` is not a legal version for a published leaf ruleset. Printing
    it as though it were an identity is how an unreproducible result gets
    filed as evidence."""
    payload = {**load("decide_terminal_eligible.json"), "ruleset_version": "unknown"}
    out = strip(run_decide(monkeypatch, tmp_path, payload).output)
    assert "Unresolved" in out
    assert "ruleset_version" in out
    assert "cannot be reproduced" in out

    ident = contract.resolved_identity(payload)
    assert not ident.is_reproducible
    assert "ruleset_version" in ident.unresolved


# ---------------------------------------------------------------------------
# Identity: explain
# ---------------------------------------------------------------------------


def test_explain_json_emits_the_whole_envelope_not_just_criteria(monkeypatch, tmp_path):
    payload = load("explain_envelope.json")
    emitted = json.loads(strip(run_explain(monkeypatch, tmp_path, payload, root=("--output", "json")).output))
    assert emitted == payload
    assert emitted["ruleset_version"] == "v1"
    assert emitted["content_digest"].startswith("sha256:")


def test_explain_human_output_shows_identity_above_the_rules(monkeypatch, tmp_path):
    payload = load("explain_envelope.json")
    out = strip(run_explain(monkeypatch, tmp_path, payload).output)
    assert payload["content_digest"] in out
    assert out.index("Ruleset identity") < out.index("Logic")


# ---------------------------------------------------------------------------
# Result / logic trace / source are three different things
# ---------------------------------------------------------------------------


def test_decide_separates_result_logic_and_sources(monkeypatch, tmp_path):
    payload = load("decide_with_source_references.json")
    out = strip(run_decide(monkeypatch, tmp_path, payload, extra=("--explain",)).output)
    assert out.index("Result") < out.index("Logic trace") < out.index("Sources")


def test_criterion_status_is_labelled_as_explanatory_not_a_verdict(monkeypatch, tmp_path):
    out = strip(
        run_decide(
            monkeypatch,
            tmp_path,
            load("decide_terminal_not_eligible.json"),
            extra=("--explain",),
        ).output
    )
    assert "Explanatory only" in out
    assert "never aggregate" in out


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def _only_reference() -> dict:
    payload = load("explain_with_source_references.json")
    for criterion in payload["criteria"]:
        if criterion.get("source_references"):
            return criterion["source_references"][0]
    raise AssertionError("fixture carries no source references")


@pytest.mark.parametrize("runner", ["decide", "explain"])
def test_source_reference_renders_every_load_bearing_member(monkeypatch, tmp_path, runner):
    reference = _only_reference()
    if runner == "decide":
        out = strip(
            run_decide(
                monkeypatch,
                tmp_path,
                load("decide_with_source_references.json"),
                extra=("--explain",),
            ).output
        )
    else:
        out = strip(run_explain(monkeypatch, tmp_path, load("explain_with_source_references.json")).output)

    assert reference["title"] in out
    assert reference["authority"] in out
    assert reference["locator"] in out
    assert reference["licence"] in out
    assert reference["content_digest"] in out
    assert reference["deep_link"] in out
    # The verbatim quote is the point of a citation -- a paraphrase would not
    # be checkable against the cited document.
    assert reference["quote"]["exact"][:60] in out


def test_both_paths_serve_the_identical_source_reference():
    """The decide and explain paths must not drift into two dialects."""
    from_explain = _only_reference()
    decide = load("decide_with_source_references.json")
    cited = list(contract.iter_explanation_sources(decide["explanation"]))
    assert cited, "decide fixture carries no nested source references"
    assert cited[0].references[0] == from_explain


def test_decide_sources_are_read_from_the_nested_decide_shape():
    """`/decide` nests criteria under explanation.groups[].criteria[];
    `/explain` returns a flat criteria array. A reader that only understands
    the flat shape is green on a fixture it could never read off the wire."""
    decide = load("decide_with_source_references.json")
    assert "groups" in decide["explanation"]
    assert "criteria" not in decide["explanation"]
    # The flat reader finds nothing in a decide explanation...
    assert list(contract.iter_criteria_sources(decide["explanation"].get("criteria"))) == []
    # ...and the nested reader finds nothing in an explain envelope.
    explain = load("explain_with_source_references.json")
    assert list(contract.iter_explanation_sources(explain)) == []
    assert list(contract.iter_criteria_sources(explain["criteria"]))


def test_missing_sources_say_so_rather_than_rendering_nothing(monkeypatch, tmp_path):
    payload = load("explain_envelope.json")
    assert not any(c.get("source_references") for c in payload["criteria"])
    out = strip(run_explain(monkeypatch, tmp_path, payload).output)
    assert "Sources" in out
    assert "No published source references" in out


def test_degraded_source_reference_is_flagged_not_smoothed_over(monkeypatch, tmp_path):
    payload = load("explain_with_degraded_source_reference.json")
    out = strip(run_explain(monkeypatch, tmp_path, payload).output)
    assert "Incomplete reference" in out
    assert "licence" in out
    assert "deep_link" in out


def test_source_reference_gaps_names_every_missing_member():
    complete = _only_reference()
    assert contract.source_reference_gaps(complete) == []
    stripped = {k: v for k, v in complete.items() if k not in ("licence", "quote")}
    assert sorted(contract.source_reference_gaps(stripped)) == ["licence", "quote"]
