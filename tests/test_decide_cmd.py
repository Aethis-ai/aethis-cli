"""Tests for `aethis decide` — in particular the --explain rendering, which
crashed in 0.12.2 because `_print_explanation` assumed a flat list of dicts
but the engine returns a nested `{groups: [{criteria: [...]}, ...]}` shape.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip(s: str) -> str:
    """Strip ANSI escape sequences so substring asserts aren't fooled by
    Rich's per-token number highlighting (e.g. `1000+` → `<ESC>1000<ESC>+`)."""
    return _ANSI_RE.sub("", s)


# Captured from a real api.aethis.ai response against
# `aethis/spacecraft-crew-certification` (trimmed to two groups with mixed
# criterion statuses + a non-empty `unused_facts`). Mirrors the contract in
# aethis-core/aethis_core/public/routes/decide.py:312-324.
_REAL_EXPLANATION = {
    "decision": "eligible",
    "decision_path": "species_eligible",
    "groups": [
        {
            "group": "species_eligibility",
            "status": "satisfied",
            "criteria": [
                {
                    "criterion_id": "species_eligible",
                    "title": "Applicant is of an eligible species",
                    "status": "satisfied",
                    "supporting_facts": [{"field": "space.crew.species", "value": "human"}],
                }
            ],
        },
        {
            "group": "flight_readiness",
            "status": "satisfied",
            "criteria": [
                {
                    "criterion_id": "flight_readiness_exempt_1000hrs",
                    "title": "Exempt from flight readiness — 1000+ flight hours",
                    "status": "satisfied",
                    "supporting_facts": [
                        {"field": "space.crew.flight_hours", "value": 1000},
                    ],
                },
                {
                    "criterion_id": "flight_readiness_exempt_age",
                    "title": "Exempt from flight readiness — age 60+",
                    "status": "not_satisfied",
                },
            ],
        },
    ],
    "unused_facts": ["space.crew.has_radiation_cert"],
}


def _decide_result(explanation):
    return {
        "decision": "eligible",
        "ruleset_id": "spacecraft-crew-certification:20260517-c59647a5",
        "fields_evaluated": 16,
        "fields_provided": 11,
        "missing_fields": [],
        "field_errors": None,
        "next_question": None,
        "optimal_path": None,
        "trace": None,
        "explanation": explanation,
    }


def test_decide_explain_renders_nested_explanation(tmp_path, monkeypatch):
    """Regression: --explain must walk the dict shape returned by the engine
    without raising AttributeError, and must surface group statuses,
    criterion titles, supporting facts, and unused fields."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)

    client = MagicMock()
    client.decide.return_value = _decide_result(_REAL_EXPLANATION)

    with patch("aethis_cli.client.AethisClient", return_value=client):
        from aethis_cli.main import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "decide",
                "-b",
                "aethis/spacecraft-crew-certification",
                "-i",
                '{"space.crew.species":"Human"}',
                "--explain",
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    assert "Traceback" not in out
    assert "AttributeError" not in out

    # Group headers must appear with their names.
    assert "species_eligibility" in out
    assert "flight_readiness" in out

    # Criterion titles must appear (both satisfied and not_satisfied paths).
    assert "Applicant is of an eligible species" in out
    assert "1000+ flight hours" in out
    assert "age 60+" in out

    # Supporting facts must appear under satisfied criteria.
    assert "space.crew.species = human" in out
    assert "space.crew.flight_hours = 1000" in out

    # Decision path is surfaced.
    assert "species_eligible" in out

    # Unused-facts block is rendered with its explanatory header.
    assert "Unused fields" in out
    assert "space.crew.has_radiation_cert" in out


def test_decide_explain_handles_missing_decision_path(tmp_path, monkeypatch):
    """not_eligible decisions omit decision_path per the engine contract;
    the CLI must render without crashing and without a 'Satisfied by:' line."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)

    explanation = {
        "decision": "not_eligible",
        "groups": [
            {
                "group": "towel_compliance",
                "status": "not_satisfied",
                "criteria": [
                    {
                        "criterion_id": "towel_compliance",
                        "title": "Applicant carries a towel",
                        "status": "not_satisfied",
                    }
                ],
            }
        ],
        "unused_facts": [],
    }

    client = MagicMock()
    client.decide.return_value = {**_decide_result(explanation), "decision": "not_eligible"}

    with patch("aethis_cli.client.AethisClient", return_value=client):
        from aethis_cli.main import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "decide",
                "-b",
                "aethis/spacecraft-crew-certification",
                "-i",
                '{"space.crew.has_towel":false}',
                "--explain",
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    assert "Traceback" not in out
    assert "Satisfied by:" not in out
    assert "Applicant carries a towel" in out
    # No unused-facts block when the list is empty.
    assert "Unused fields" not in out
