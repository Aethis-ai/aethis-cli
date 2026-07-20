"""Tests for `aethis review` — the Authoring Coach report render + flag plumbing.

The command is a thin client over `POST /projects/{id}/review`; these tests mock
the authed client so they exercise resolution, flag handling, and the human /
JSON render without a network or a real key.
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _report(**overrides) -> dict:
    """A representative ReviewReport (mirrors aethis-core's response model)."""
    base = {
        "project_id": "proj_abc",
        "rubric_version": "1.0.0",
        "score": 72,
        "data_completeness": "ok",
        "checks": [
            {
                "id": "G1",
                "group": "grounding",
                "audience": "author",
                "actionable_via": "sources",
                "status": "pass",
                "evidence": "3 sources cited",
                "weight": 3,
                "scored": True,
                "why": "Rules trace back to source text.",
                "docs_url": "https://docs.aethis.ai/authoring/review-checks#g1",
            },
            {
                "id": "P2",
                "group": "process",
                "audience": "author",
                "actionable_via": "tests",
                "status": "warn",
                "evidence": "4 test cases",
                "weight": 2,
                "scored": True,
                "why": "More edge cases would raise confidence.",
                "docs_url": "https://docs.aethis.ai/authoring/review-checks#p2",
            },
        ],
        "strengths": ["Every rule cites a source", "Deterministic test suite is green"],
        "next_skill": {
            "check_id": "P2",
            "message": "Add a few boundary test cases to pin the edges.",
            "actionable_via": "tests",
            "docs_url": "https://docs.aethis.ai/authoring/review-checks#p2",
        },
        "coaching": None,
    }
    base.update(overrides)
    return base


def _run(args, report, monkeypatch, tmp_path, *, capture_client=None):
    """Invoke `aethis review` with a mocked authed client returning `report`."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty_xdg"))

    client = MagicMock()
    client.review.return_value = report

    def _fake_make_authed_client(api_key, base_url, **kwargs):
        if capture_client is not None:
            capture_client["api_key"] = api_key
            capture_client["base_url"] = base_url
            capture_client.update(kwargs)
        return client

    from aethis_cli.main import app

    with patch("aethis_cli.commands.review_cmd.make_authed_client", _fake_make_authed_client):
        runner = CliRunner()
        result = runner.invoke(app, ["review"] + args, catch_exceptions=False)
    return result, client


def test_review_default_render(tmp_path, monkeypatch):
    """Default render: score headline, strengths, and the single next step."""
    result, client = _run(["proj_abc"], _report(), monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    assert "72/100" in out
    # Strengths (2-3, evidence-cited).
    assert "Every rule cites a source" in out
    # The single next skill: its message + docs link.
    assert "Add a few boundary test cases" in out
    assert "docs.aethis.ai/authoring/review-checks#p2" in out
    # Default (no --coach) sent coach=false.
    client.review.assert_called_once_with("proj_abc", coach=False)


def test_review_json_is_parseable(tmp_path, monkeypatch):
    """--json emits the raw ReviewReport as valid, parseable JSON."""
    result, _ = _run(["proj_abc", "--json"], _report(), monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["project_id"] == "proj_abc"
    assert payload["score"] == 72
    assert payload["next_skill"]["check_id"] == "P2"


def test_review_thin_project_empty_state(tmp_path, monkeypatch):
    """A thin project degrades to a friendly 'just getting started' render with
    no score and the server's 'add a source' next step — never a crash."""
    thin = _report(
        score=None,
        data_completeness="thin",
        strengths=[],
        next_skill={
            "check_id": "G1",
            "message": "Add a source document to begin.",
            "actionable_via": "sources",
            "docs_url": "https://docs.aethis.ai/authoring/review-checks#g1",
        },
    )
    result, _ = _run(["proj_abc"], thin, monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    assert "just getting started" in out
    assert "Add a source document to begin." in out
    assert "Traceback" not in out


def test_review_low_score_still_exit_zero(tmp_path, monkeypatch):
    """Advisory tool: even a low score exits 0 (never a merge gate)."""
    result, _ = _run(["proj_abc"], _report(score=12), monkeypatch, tmp_path)
    assert result.exit_code == 0, result.output
    assert "12/100" in _strip(result.output)


def test_review_verbose_shows_check_table(tmp_path, monkeypatch):
    """--verbose renders the full per-check table (check ids + statuses)."""
    result, _ = _run(["proj_abc", "--verbose"], _report(), monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    assert "G1" in out
    assert "P2" in out
    assert "PASS" in out
    assert "WARN" in out


def test_review_coach_plumbs_anthropic_key(tmp_path, monkeypatch):
    """--coach with ANTHROPIC_API_KEY set builds the client with the key and
    calls review(coach=True)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    captured: dict = {}
    result, client = _run(
        ["proj_abc", "--coach"],
        _report(coaching="You're close — a couple of edge tests will seal it."),
        monkeypatch,
        tmp_path,
        capture_client=captured,
    )

    assert result.exit_code == 0, result.output
    assert captured.get("anthropic_key") == "sk-ant-test"
    client.review.assert_called_once_with("proj_abc", coach=True)
    assert "You're close" in _strip(result.output)


def test_review_coach_without_key_warns_but_proceeds(tmp_path, monkeypatch):
    """--coach with no Anthropic key warns (internal keys still work) and still
    issues the request rather than failing fast."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    captured: dict = {}
    result, client = _run(["proj_abc", "--coach"], _report(), monkeypatch, tmp_path, capture_client=captured)

    assert result.exit_code == 0, result.output
    assert captured.get("anthropic_key") is None
    client.review.assert_called_once_with("proj_abc", coach=True)
    assert "Anthropic key" in _strip(result.output)
