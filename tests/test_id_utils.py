"""Tests for commands/_id_utils — ruleset vs project ID classification."""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from aethis_cli.commands._id_utils import classify_id, require_ruleset_id


def test_classify_project_id():
    assert classify_id("proj_i1HyinBtFJniayUC") == "project"
    assert classify_id("proj_") == "project"


def test_classify_ruleset_id():
    assert classify_id("crew_certification:20260408-cbf63f1f") == "ruleset"
    assert classify_id("example:v1") == "ruleset"


def test_classify_unknown_id():
    assert classify_id("just-a-string") == "unknown"
    assert classify_id("") == "unknown"


def test_classify_project_prefix_wins_over_colon():
    # Shouldn't happen in practice, but prefix is the stronger signal.
    assert classify_id("proj_has:colon") == "project"


def test_classify_slug_two_segments():
    assert classify_id("aethis/construction-all-risks") == "slug"
    assert classify_id("aethis/spacecraft-crew-certification") == "slug"


def test_classify_slug_three_segments():
    assert classify_id("aethis/uk-fsm/universal-infant") == "slug"
    assert classify_id("aethis/uk-fsm/household-criteria") == "slug"


def test_classify_ruleset_id_wins_over_slug():
    # A value with both `:` and `/` is treated as a ruleset ID — the API
    # never returns slugs containing colons, so this disambiguation is safe.
    assert classify_id("ns/name:20260101-deadbeef") == "ruleset"


def _invoke_require(value: str):
    app = typer.Typer(pretty_exceptions_enable=False)

    @app.command()
    def root(v: str):
        require_ruleset_id(v)

    runner = CliRunner()
    return runner.invoke(app, [value], catch_exceptions=False)


def test_require_bundle_id_accepts_ruleset_shape():
    result = _invoke_require("crew_certification:20260408-cbf63f1f")
    assert result.exit_code == 0


def test_require_ruleset_id_accepts_slug():
    result = _invoke_require("aethis/uk-fsm/universal-infant")
    assert result.exit_code == 0


def test_require_ruleset_id_accepts_two_segment_slug():
    result = _invoke_require("aethis/construction-all-risks")
    assert result.exit_code == 0


def test_require_ruleset_id_rejects_project_id_with_hint():
    result = _invoke_require("proj_i1HyinBtFJniayUC")
    assert result.exit_code == 1
    assert "Project ID" in result.output
    assert "aethis projects list" in result.output


def test_require_ruleset_id_rejects_unknown_with_hint():
    result = _invoke_require("random-garbage")
    assert result.exit_code == 1
    assert "not a valid Ruleset ID or slug" in result.output
    assert "<namespace>/<name>" in result.output
