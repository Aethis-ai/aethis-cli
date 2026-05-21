"""Tests for Phase B.1b — ruleset lifecycle commands scoped to a rulebook.

Covers:
  - `aethis rulesets list <rulebook>` (new rulebook-scoped mode)
  - `aethis rulesets create <rulebook> <ruleset_name>` (new)
  - `aethis rulesets show <rulebook> <ruleset_name>` (new)
  - `aethis rulesets promote-to-live <rulebook> <ruleset_name> <ruleset_id>` (new)

Pattern follows existing `tests/test_rulebooks_cmd.py`: CliRunner + patch
the AethisClient class so the full command/dispatch path is exercised
without hitting the network.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _runner_invoke(args, env=None):
    from aethis_cli.main import app

    runner = CliRunner()
    return runner.invoke(app, args, catch_exceptions=False, env=env or {})


# ---------------------------------------------------------------------------
# rulesets list <rulebook> — new rulebook-scoped mode
# ---------------------------------------------------------------------------


def test_rulesets_list_with_rulebook_arg_uses_rulebook_endpoint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.setenv("COLUMNS", "200")

    client = MagicMock()
    client.list_rulesets_in_rulebook.return_value = {
        "rulebook_id": "rb_abc",
        "rulesets": [
            {
                "ruleset_name": "child_eligibility",
                "display_name": "Child eligibility",
                "version_count": 3,
                "live_version": "v2",
                "states": ["archived", "live", "draft"],
            },
            {
                "ruleset_name": "household_criteria",
                "display_name": "Household criteria",
                "version_count": 1,
                "live_version": None,
                "states": ["testing"],
            },
        ],
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulesets", "list", "aethis/uk-fsm"])

    assert result.exit_code == 0, result.output
    client.list_rulesets_in_rulebook.assert_called_once_with("aethis/uk-fsm")
    out = _strip(result.output)
    assert "child_eligibility" in out
    assert "household_criteria" in out
    assert "Child eligibility" in out
    assert "v2" in out
    # Legacy project endpoint must not be hit.
    client.list_rulesets.assert_not_called()


def test_rulesets_list_with_rulebook_empty_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.list_rulesets_in_rulebook.return_value = {
        "rulebook_id": "rb_x",
        "rulesets": [],
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulesets", "list", "rb_x"])
    assert result.exit_code == 0
    assert "No rulesets in rulebook" in _strip(result.output)
    assert "aethis rulesets create" in _strip(result.output)


def test_rulesets_list_no_args_falls_through_to_showcase(tmp_path, monkeypatch):
    """Backward compat: no rulebook arg + no project context = public showcase."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    client = MagicMock()
    client.list_public_rulesets.return_value = []
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulesets", "list"])
    assert result.exit_code == 0
    client.list_rulesets_in_rulebook.assert_not_called()


# ---------------------------------------------------------------------------
# rulesets create <rulebook> <ruleset_name>
# ---------------------------------------------------------------------------


def test_rulesets_create_uses_default_display_name(tmp_path, monkeypatch):
    """Without `-n`, display name is auto-derived from ruleset_name."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.create_ruleset_in_rulebook.return_value = {
        "rulebook_id": "rb_abc",
        "ruleset_name": "child_eligibility",
        "bundle_id": "rs_xyz",
        "state": "draft",
        "name": "Child Eligibility",
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulesets", "create", "aethis/uk-fsm", "child_eligibility"])
    assert result.exit_code == 0, result.output
    client.create_ruleset_in_rulebook.assert_called_once_with(
        "aethis/uk-fsm",
        ruleset_name="child_eligibility",
        name="Child Eligibility",
    )
    assert "rs_xyz" in _strip(result.output)


def test_rulesets_create_explicit_display_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.create_ruleset_in_rulebook.return_value = {
        "rulebook_id": "rb_abc",
        "ruleset_name": "household_criteria",
        "bundle_id": "rs_a",
        "state": "draft",
        "name": "Household qualifying criteria",
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(
            [
                "rulesets",
                "create",
                "rb_abc",
                "household_criteria",
                "-n",
                "Household qualifying criteria",
            ]
        )
    assert result.exit_code == 0
    client.create_ruleset_in_rulebook.assert_called_once_with(
        "rb_abc",
        ruleset_name="household_criteria",
        name="Household qualifying criteria",
    )


# ---------------------------------------------------------------------------
# rulesets show <rulebook> <ruleset_name>
# ---------------------------------------------------------------------------


def test_rulesets_show_renders_versions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.setenv("COLUMNS", "200")
    client = MagicMock()
    client.show_ruleset_in_rulebook.return_value = {
        "rulebook_id": "rb_abc",
        "ruleset_name": "child_eligibility",
        "display_name": "Child eligibility (FSM, England)",
        "versions": [
            {
                "bundle_id": "rs_v1",
                "version": "v1",
                "state": "archived",
                "created_at": "2026-04-08T12:00:00Z",
            },
            {
                "bundle_id": "rs_v2",
                "version": "v2",
                "state": "live",
                "created_at": "2026-05-15T12:00:00Z",
            },
            {
                "bundle_id": "rs_v3",
                "version": "v3",
                "state": "draft",
                "created_at": "2026-05-21T12:00:00Z",
            },
        ],
        "live_version": "v2",
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulesets", "show", "aethis/uk-fsm", "child_eligibility"])
    assert result.exit_code == 0, result.output
    client.show_ruleset_in_rulebook.assert_called_once_with("aethis/uk-fsm", "child_eligibility")
    out = _strip(result.output)
    assert "child_eligibility" in out
    assert "live version: v2" in out
    assert "rs_v1" in out and "rs_v2" in out and "rs_v3" in out
    assert "archived" in out and "live" in out and "draft" in out


def test_rulesets_show_no_live_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.show_ruleset_in_rulebook.return_value = {
        "rulebook_id": "rb_abc",
        "ruleset_name": "english_language",
        "display_name": None,
        "versions": [
            {"bundle_id": "rs_a", "version": "v1", "state": "testing", "created_at": ""},
        ],
        "live_version": None,
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulesets", "show", "rb_abc", "english_language"])
    assert result.exit_code == 0
    out = _strip(result.output)
    assert "no live version" in out
    assert "rs_a" in out


# ---------------------------------------------------------------------------
# rulesets promote-to-live <rulebook> <ruleset_name> <ruleset_id>
# ---------------------------------------------------------------------------


def test_promote_to_live_happy_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.promote_ruleset_to_live.return_value = {
        "rulebook_id": "rb_abc",
        "ruleset_name": "child_eligibility",
        "promoted_ruleset_id": "rs_v3",
        "new_rulebook_version": 7,
        "prior_live_archived_id": "rs_v2",
        "cut_reason": "auto_on_promote",
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(
            [
                "rulesets",
                "promote-to-live",
                "aethis/uk-fsm",
                "child_eligibility",
                "rs_v3",
            ]
        )
    assert result.exit_code == 0, result.output
    client.promote_ruleset_to_live.assert_called_once_with(
        "aethis/uk-fsm",
        "child_eligibility",
        ruleset_id="rs_v3",
        note=None,
    )
    out = _strip(result.output)
    assert "Promoted" in out
    assert "v7" in out
    assert "rs_v2" in out  # prior live archived
    assert "auto_on_promote" in out


def test_promote_to_live_with_note(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.promote_ruleset_to_live.return_value = {
        "rulebook_id": "rb_x",
        "ruleset_name": "rs",
        "promoted_ruleset_id": "rs_a",
        "new_rulebook_version": 2,
        "prior_live_archived_id": None,
        "cut_reason": "auto_on_promote",
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(
            [
                "rulesets",
                "promote-to-live",
                "rb_x",
                "rs",
                "rs_a",
                "--note",
                "post-2026-04 statutory update",
            ]
        )
    assert result.exit_code == 0
    client.promote_ruleset_to_live.assert_called_once_with(
        "rb_x",
        "rs",
        ruleset_id="rs_a",
        note="post-2026-04 statutory update",
    )


def test_promote_to_live_first_promotion_has_no_prior_live(tmp_path, monkeypatch):
    """When a ruleset has never been live before, prior_live_archived_id is null."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.promote_ruleset_to_live.return_value = {
        "rulebook_id": "rb_x",
        "ruleset_name": "rs",
        "promoted_ruleset_id": "rs_a",
        "new_rulebook_version": 1,
        "prior_live_archived_id": None,
        "cut_reason": "auto_on_promote",
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulesets", "promote-to-live", "rb_x", "rs", "rs_a"])
    assert result.exit_code == 0
    out = _strip(result.output)
    assert "v1" in out
    # No "prior live archived" line should appear.
    assert "prior live archived" not in out


# ---------------------------------------------------------------------------
# API error propagation
# ---------------------------------------------------------------------------


def test_promote_to_live_surfaces_422(tmp_path, monkeypatch):
    """Engine 422 (e.g. ruleset not in testing state) propagates as exit 1."""
    from aethis_cli.errors import AethisAPIError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.promote_ruleset_to_live.side_effect = AethisAPIError(
        status_code=422,
        detail={
            "error": "validation_error",
            "reason_code": "ruleset_not_promotable",
            "message": "ruleset 'rs_x' must be in 'testing' state",
        },
    )
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulesets", "promote-to-live", "rb_x", "child_eligibility", "rs_x"])
    assert result.exit_code == 1
    out = _strip(result.output)
    assert "testing" in out or "ruleset_not_promotable" in out
