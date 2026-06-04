"""Tests for `aethis rulebooks` — the converged 2-term authoring model's
top-level command group.

Pattern follows existing `tests/test_decide_cmd.py`: CliRunner + patch the
AethisClient class so we exercise the full command/dispatch path without
hitting the network.
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _runner_invoke(args, env=None):
    """Boilerplate: import the app, patch the client, run."""
    from aethis_cli.main import app

    runner = CliRunner()
    return runner.invoke(app, args, catch_exceptions=False, env=env or {})


# ---------------------------------------------------------------------------
# rulebooks list
# ---------------------------------------------------------------------------


def test_rulebooks_list_renders_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.setenv("COLUMNS", "200")

    client = MagicMock()
    client.list_rulebooks.return_value = [
        {
            "rulebook_id": "rb_abc",
            "slug": "aethis/uk-fsm",
            "name": "UK Free School Meals",
            "domain": "uk_fsm",
            "status": "active",
            "ruleset_refs": [{"section_id": "a"}, {"section_id": "b"}],
        },
        {
            "rulebook_id": "rb_def",
            "slug": None,
            "name": "Spacecraft Crew",
            "domain": "",
            "status": "draft",
            "ruleset_refs": [],
        },
    ]
    with (
        patch("aethis_cli.client.AethisClient", return_value=client),
        patch(
            "aethis_cli.commands.rulebooks_cmd.make_anonymous_client",
            return_value=_patch_anonymous([]),
        ),
    ):
        result = _runner_invoke(["rulebooks", "list"])

    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    # Slugged rulebooks show the slug as their identifier; the raw id stays
    # in `--json` / `rulebooks show`.
    assert "aethis/uk-fsm" in out
    assert "rb_abc" not in out
    assert "UK Free School Meals" in out
    # Unslugged rulebooks fall back to the rulebook_id.
    assert "rb_def" in out
    assert "Spacecraft Crew" in out


def test_rulebooks_list_empty_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.list_rulebooks.return_value = []
    with (
        patch("aethis_cli.client.AethisClient", return_value=client),
        patch(
            "aethis_cli.commands.rulebooks_cmd.make_anonymous_client",
            return_value=_patch_anonymous([]),
        ),
    ):
        result = _runner_invoke(["rulebooks", "list"])
    assert result.exit_code == 0
    assert "No rulebooks yet" in _strip(result.output)


def test_rulebooks_list_keyed_includes_public_catalogue(tmp_path, monkeypatch):
    """A keyed user still sees public rulebooks — the catalogue is part of
    the product surface, not an anonymous-only fallback. Entries the tenant
    already owns are deduped out of the public section."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.setenv("COLUMNS", "200")

    client = MagicMock()
    client.list_rulebooks.return_value = [
        {
            "rulebook_id": "rb_mine",
            "slug": "acme/my-draft",
            "name": "My Draft",
            "domain": "acme",
            "status": "draft",
            "ruleset_refs": [],
        },
    ]
    anon_client = _patch_anonymous(
        [
            {
                "rulebook_id": "rb_pub",
                "slug": "aethis/uk-fsm",
                "name": "UK Free School Meals",
                "domain": "uk_fsm",
                "status": "active",
                "visibility": "public",
                "ruleset_refs": [],
            },
            # Duplicate of a tenant-owned rulebook — must not render twice.
            {
                "rulebook_id": "rb_mine",
                "slug": "acme/my-draft",
                "name": "My Draft",
                "domain": "acme",
                "status": "draft",
                "ruleset_refs": [],
            },
        ]
    )

    with (
        patch("aethis_cli.client.AethisClient", return_value=client),
        patch("aethis_cli.commands.rulebooks_cmd.make_anonymous_client", return_value=anon_client),
    ):
        result = _runner_invoke(["rulebooks", "list"])

    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    assert "acme/my-draft" in out
    assert "aethis/uk-fsm" in out
    assert "yours" in out
    assert "public" in out
    assert out.count("acme/my-draft") == 1


def test_rulebooks_list_keyed_empty_tenant_still_shows_public(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.setenv("COLUMNS", "200")

    client = MagicMock()
    client.list_rulebooks.return_value = []
    anon_client = _patch_anonymous(
        [
            {
                "rulebook_id": "rb_pub",
                "slug": "aethis/uk-fsm",
                "name": "UK Free School Meals",
                "domain": "uk_fsm",
                "status": "active",
                "ruleset_refs": [],
            },
        ]
    )

    with (
        patch("aethis_cli.client.AethisClient", return_value=client),
        patch("aethis_cli.commands.rulebooks_cmd.make_anonymous_client", return_value=anon_client),
    ):
        result = _runner_invoke(["rulebooks", "list"])

    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    assert "No rulebooks in your tenant yet" in out
    assert "aethis/uk-fsm" in out


def test_rulebooks_list_public_flag_skips_auth(tmp_path, monkeypatch):
    """`--public` shows only the anonymous catalogue, even with a key cached."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.setenv("COLUMNS", "200")

    anon_client = _patch_anonymous(
        [
            {
                "rulebook_id": "rb_pub",
                "slug": "aethis/uk-fsm",
                "name": "UK Free School Meals",
                "domain": "uk_fsm",
                "status": "active",
                "ruleset_refs": [],
            }
        ]
    )
    with (
        patch("aethis_cli.commands.rulebooks_cmd.make_anonymous_client", return_value=anon_client),
        patch("aethis_cli.commands.rulebooks_cmd.load_client_or_fallback") as load_client,
    ):
        result = _runner_invoke(["rulebooks", "list", "--public"])

    assert result.exit_code == 0, result.output
    assert "aethis/uk-fsm" in _strip(result.output)
    load_client.assert_not_called()


def test_rulebooks_list_keyed_survives_public_catalogue_error(tmp_path, monkeypatch):
    """A catalogue outage must not take down the tenant listing — warn and
    render what we have."""
    from aethis_cli.errors import AethisAPIError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.setenv("COLUMNS", "200")

    client = MagicMock()
    client.list_rulebooks.return_value = [
        {
            "rulebook_id": "rb_mine",
            "slug": "acme/my-draft",
            "name": "My Draft",
            "domain": "acme",
            "status": "draft",
            "ruleset_refs": [],
        },
    ]
    anon_client = _patch_anonymous([])
    anon_client.list_public_rulebooks.side_effect = AethisAPIError(status_code=503, detail="upstream down")

    with (
        patch("aethis_cli.client.AethisClient", return_value=client),
        patch("aethis_cli.commands.rulebooks_cmd.make_anonymous_client", return_value=anon_client),
    ):
        result = _runner_invoke(["rulebooks", "list"])

    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    assert "acme/my-draft" in out
    assert "Could not fetch the public catalogue" in out


# ---------------------------------------------------------------------------
# rulebooks show
# ---------------------------------------------------------------------------


def test_rulebooks_show_prints_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.get_rulebook.return_value = {
        "rulebook_id": "rb_abc",
        "name": "UK FSM",
        "ruleset_refs": [],
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "show", "rb_abc"])
    assert result.exit_code == 0, result.output
    client.get_rulebook.assert_called_once_with("rb_abc")
    assert '"rulebook_id"' in result.output
    assert "rb_abc" in result.output


# ---------------------------------------------------------------------------
# rulebooks create
# ---------------------------------------------------------------------------


def test_rulebooks_create_with_all_options(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")

    client = MagicMock()
    client.create_rulebook.return_value = {
        "rulebook_id": "rb_new",
        "slug": "aethis/uk-fsm",
        "name": "UK FSM",
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(
            [
                "rulebooks",
                "create",
                "UK FSM",
                "--domain",
                "uk_fsm",
                "--slug",
                "aethis/uk-fsm",
                "--description",
                "Free school meals eligibility",
            ]
        )

    assert result.exit_code == 0, result.output
    client.create_rulebook.assert_called_once_with(
        name="UK FSM",
        domain="uk_fsm",
        slug="aethis/uk-fsm",
        description="Free school meals eligibility",
    )
    assert "rb_new" in _strip(result.output)
    assert "aethis/uk-fsm" in _strip(result.output)


def test_rulebooks_create_minimal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.create_rulebook.return_value = {"rulebook_id": "rb_min"}
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "create", "Bare bones"])
    assert result.exit_code == 0
    client.create_rulebook.assert_called_once_with(name="Bare bones", domain="", slug=None, description=None)


# ---------------------------------------------------------------------------
# rulebooks set-fields
# ---------------------------------------------------------------------------


def test_set_fields_from_json_top_level_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")

    fields_path = tmp_path / "fields.json"
    fields_path.write_text(
        json.dumps(
            [
                {"key": "applicant.age", "sort": "Int"},
                {
                    "key": "child.year_group",
                    "sort": "Enum",
                    "enum_values": ["reception", "year_1"],
                },
            ]
        )
    )

    client = MagicMock()
    client.set_rulebook_fields.return_value = {
        "rulebook_id": "rb_x",
        "fields": [
            {"key": "applicant.age", "sort": "Int"},
            {
                "key": "child.year_group",
                "sort": "Enum",
                "enum_values": ["reception", "year_1"],
            },
        ],
        "field_lock_state": "unlocked",
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "set-fields", "rb_x", "-f", str(fields_path)])

    assert result.exit_code == 0, result.output
    args, _kwargs = client.set_rulebook_fields.call_args
    assert args[0] == "rb_x"
    sent_fields = args[1]
    assert isinstance(sent_fields, list)
    assert len(sent_fields) == 2
    assert sent_fields[0]["key"] == "applicant.age"


def test_set_fields_from_json_wrapped_object(tmp_path, monkeypatch):
    """Accept both a top-level list and {fields: [...]}."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    fields_path = tmp_path / "fields.json"
    fields_path.write_text(json.dumps({"fields": [{"key": "x", "sort": "Bool"}]}))
    client = MagicMock()
    client.set_rulebook_fields.return_value = {
        "fields": [{"key": "x", "sort": "Bool"}],
        "field_lock_state": "unlocked",
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "set-fields", "rb_x", "-f", str(fields_path)])
    assert result.exit_code == 0
    sent = client.set_rulebook_fields.call_args[0][1]
    assert sent == [{"key": "x", "sort": "Bool"}]


def test_set_fields_rejects_empty_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    fields_path = tmp_path / "fields.json"
    fields_path.write_text("[]")
    client = MagicMock()
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "set-fields", "rb_x", "-f", str(fields_path)])
    assert result.exit_code != 0
    assert "non-empty" in _strip(result.output).lower()


# ---------------------------------------------------------------------------
# rulebooks lock-fields / unlock-fields / get-fields
# ---------------------------------------------------------------------------


def test_lock_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.lock_rulebook_fields.return_value = {
        "field_lock_state": "locked",
        "fields": [{"key": "x", "sort": "Bool"}, {"key": "y", "sort": "Int"}],
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "lock-fields", "rb_x"])
    assert result.exit_code == 0, result.output
    client.lock_rulebook_fields.assert_called_once_with("rb_x")
    assert "Locked" in _strip(result.output)


def test_unlock_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.unlock_rulebook_fields.return_value = {"field_lock_state": "unlocked"}
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "unlock-fields", "rb_x"])
    assert result.exit_code == 0
    client.unlock_rulebook_fields.assert_called_once_with("rb_x")


def test_get_fields_renders_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.setenv("COLUMNS", "200")
    client = MagicMock()
    client.get_rulebook_fields.return_value = {
        "field_lock_state": "locked",
        "fields": [
            {"key": "applicant.age", "sort": "Int"},
            {
                "key": "child.year_group",
                "sort": "Enum",
                "enum_values": ["reception", "year_1"],
            },
        ],
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "get-fields", "rb_x"])
    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    assert "applicant.age" in out
    assert "Int" in out
    assert "child.year_group" in out
    assert "Enum" in out
    assert "reception" in out
    assert "locked" in out.lower()


def test_get_fields_empty_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.get_rulebook_fields.return_value = {
        "field_lock_state": "unlocked",
        "fields": [],
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "get-fields", "rb_x"])
    assert result.exit_code == 0
    assert "No fields locked yet" in _strip(result.output)


# ---------------------------------------------------------------------------
# rulebooks set-logic
# ---------------------------------------------------------------------------


def _logic_expr() -> dict:
    """The composition expression that the UK FSM example uses.

    Plain English: ``child_eligibility AND (household_criteria OR universal_infant)``.
    The shape is the Expr AST the server validates against.
    """
    return {
        "type": "op",
        "operator": "and",
        "args": [
            {"type": "field_ref", "key": "child_eligibility"},
            {
                "type": "op",
                "operator": "or",
                "args": [
                    {"type": "field_ref", "key": "household_criteria"},
                    {"type": "field_ref", "key": "universal_infant"},
                ],
            },
        ],
    }


def test_set_logic_from_json_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")

    logic_path = tmp_path / "logic.json"
    logic_path.write_text(json.dumps(_logic_expr()))

    client = MagicMock()
    client.update_rulebook.return_value = {
        "rulebook_id": "rb_x",
        "outcome_logic": _logic_expr(),
    }

    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "set-logic", "rb_x", "--file", str(logic_path)])

    assert result.exit_code == 0, result.output
    client.update_rulebook.assert_called_once()
    _args, kwargs = client.update_rulebook.call_args
    assert kwargs["outcome_logic"] == _logic_expr()
    # Only outcome_logic should have been sent — no accidental name/slug edits.
    for k in ("name", "description", "ruleset_refs", "slug"):
        assert kwargs.get(k) is None, f"unexpected {k} sent on set-logic"
    out = _strip(result.output)
    assert "rb_x" in out


def test_set_logic_from_yaml_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    import importlib.util

    if importlib.util.find_spec("yaml") is None:
        import pytest

        pytest.skip("PyYAML not installed")

    logic_path = tmp_path / "logic.yaml"
    logic_path.write_text(
        "type: op\n"
        "operator: and\n"
        "args:\n"
        "  - type: field_ref\n"
        "    key: child_eligibility\n"
        "  - type: op\n"
        "    operator: or\n"
        "    args:\n"
        "      - type: field_ref\n"
        "        key: household_criteria\n"
        "      - type: field_ref\n"
        "        key: universal_infant\n"
    )

    client = MagicMock()
    client.update_rulebook.return_value = {"rulebook_id": "rb_x"}
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "set-logic", "rb_x", "-f", str(logic_path)])

    assert result.exit_code == 0, result.output
    sent = client.update_rulebook.call_args.kwargs["outcome_logic"]
    assert sent == _logic_expr()


def test_set_logic_inline_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")

    client = MagicMock()
    client.update_rulebook.return_value = {"rulebook_id": "rb_x"}
    inline = json.dumps(_logic_expr())
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "set-logic", "rb_x", "--logic", inline])

    assert result.exit_code == 0, result.output
    sent = client.update_rulebook.call_args.kwargs["outcome_logic"]
    assert sent == _logic_expr()


def test_set_logic_requires_one_of_file_or_logic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "set-logic", "rb_x"])
    assert result.exit_code != 0
    out = _strip(result.output).lower()
    assert "--file" in out or "--logic" in out
    client.update_rulebook.assert_not_called()


def test_set_logic_rejects_both_file_and_inline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    logic_path = tmp_path / "logic.json"
    logic_path.write_text(json.dumps(_logic_expr()))
    client = MagicMock()
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(
            [
                "rulebooks",
                "set-logic",
                "rb_x",
                "--file",
                str(logic_path),
                "--logic",
                json.dumps(_logic_expr()),
            ]
        )
    assert result.exit_code != 0
    client.update_rulebook.assert_not_called()


def test_set_logic_rejects_non_object_payload(tmp_path, monkeypatch):
    """The Expr AST must be a JSON object (dict). Lists, scalars, etc. are
    rejected at the client side so the server doesn't have to."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    bad_path = tmp_path / "logic.json"
    bad_path.write_text(json.dumps(["not", "an", "expr"]))
    client = MagicMock()
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "set-logic", "rb_x", "-f", str(bad_path)])
    assert result.exit_code != 0
    client.update_rulebook.assert_not_called()


def test_set_logic_inline_json_invalid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "set-logic", "rb_x", "--logic", "{not-json}"])
    assert result.exit_code != 0
    client.update_rulebook.assert_not_called()


# ---------------------------------------------------------------------------
# rulebooks archive
# ---------------------------------------------------------------------------


def test_archive_rulebook_with_yes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.archive_rulebook.return_value = {"status": "archived"}
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "archive", "rb_x", "--yes"])
    assert result.exit_code == 0
    client.archive_rulebook.assert_called_once_with("rb_x")


def test_archive_rulebook_aborts_without_confirmation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    with patch("aethis_cli.client.AethisClient", return_value=client):
        # Decline the confirmation prompt by feeding "n\n" on stdin.
        from aethis_cli.main import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["rulebooks", "archive", "rb_x"],
            input="n\n",
            catch_exceptions=False,
        )
    assert result.exit_code != 0
    client.archive_rulebook.assert_not_called()


# ---------------------------------------------------------------------------
# rulebooks activate
# ---------------------------------------------------------------------------


def test_activate_rulebook(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.activate_rulebook.return_value = {"status": "active"}
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "activate", "rb_x"])
    assert result.exit_code == 0
    client.activate_rulebook.assert_called_once_with("rb_x")
    assert "active" in _strip(result.output)


# ---------------------------------------------------------------------------
# rulebooks decide
# ---------------------------------------------------------------------------


def test_decide_with_inline_inputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.decide_rulebook.return_value = {"decision": "eligible"}
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(
            [
                "rulebooks",
                "decide",
                "aethis/uk-fsm",
                "-i",
                '{"applicant.age": 6}',
            ]
        )
    assert result.exit_code == 0, result.output
    args, kwargs = client.decide_rulebook.call_args
    assert args[0] == "aethis/uk-fsm"
    assert args[1] == {"applicant.age": 6}
    assert "eligible" in result.output


def test_decide_with_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    persona = tmp_path / "persona.json"
    persona.write_text(json.dumps({"applicant.age": 7}))
    client = MagicMock()
    client.decide_rulebook.return_value = {"decision": "not_eligible"}
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(
            [
                "rulebooks",
                "decide",
                "aethis/uk-fsm",
                "--input-file",
                str(persona),
                "--explain",
            ]
        )
    assert result.exit_code == 0, result.output
    args, kwargs = client.decide_rulebook.call_args
    assert args[1] == {"applicant.age": 7}
    assert kwargs.get("include_explanation") is True


def test_decide_requires_input(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "decide", "rb_x"])
    assert result.exit_code != 0
    assert "field values" in _strip(result.output).lower() or "inputs" in _strip(result.output).lower()


def test_decide_rejects_non_object_inputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "decide", "rb_x", "-i", "[1, 2, 3]"])
    assert result.exit_code != 0
    assert "object" in _strip(result.output).lower() or "mapping" in _strip(result.output).lower()


# ---------------------------------------------------------------------------
# rulebooks tests add / list / delete
# ---------------------------------------------------------------------------


def test_tests_add_single_case(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "Reception, low income",
                "field_values": {"applicant.age": 5, "household.income": 8000},
                "expected_outcome": "eligible",
            }
        )
    )
    client = MagicMock()
    client.add_rulebook_test_case.return_value = {
        "tc_id": "tc_abc",
        "name": "Reception, low income",
        "expected_outcome": "eligible",
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "tests", "add", "rb_x", "-f", str(case_path)])
    assert result.exit_code == 0, result.output
    client.add_rulebook_test_case.assert_called_once()
    _args, kwargs = client.add_rulebook_test_case.call_args
    assert kwargs["name"] == "Reception, low income"
    assert kwargs["expected_outcome"] == "eligible"
    assert "tc_abc" in _strip(result.output)


def test_tests_add_multiple_cases(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "name": "A",
                    "field_values": {"x": 1},
                    "expected_outcome": "eligible",
                },
                {
                    "name": "B",
                    "field_values": {"x": 2},
                    "expected_outcome": "not_eligible",
                },
            ]
        )
    )
    client = MagicMock()
    client.add_rulebook_test_case.side_effect = [
        {"tc_id": "tc_a", "name": "A", "expected_outcome": "eligible"},
        {"tc_id": "tc_b", "name": "B", "expected_outcome": "not_eligible"},
    ]
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "tests", "add", "rb_x", "-f", str(cases_path)])
    assert result.exit_code == 0, result.output
    assert client.add_rulebook_test_case.call_count == 2
    out = _strip(result.output)
    assert "Added 2 test case" in out
    assert "tc_a" in out and "tc_b" in out


def test_tests_add_missing_required_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "No outcome", "field_values": {}}))  # missing expected_outcome
    client = MagicMock()
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "tests", "add", "rb_x", "-f", str(bad)])
    assert result.exit_code != 0
    client.add_rulebook_test_case.assert_not_called()


def test_tests_list_renders_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.setenv("COLUMNS", "200")
    client = MagicMock()
    client.list_rulebook_test_cases.return_value = {
        "test_cases": [
            {
                "tc_id": "tc_a",
                "name": "Reception",
                "expected_outcome": "eligible",
                "field_values": {"x": 1, "y": 2},
            },
            {
                "tc_id": "tc_b",
                "name": "Year 6, high income",
                "expected_outcome": "not_eligible",
                "field_values": {"x": 3},
            },
        ]
    }
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "tests", "list", "rb_x"])
    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    assert "tc_a" in out
    assert "Reception" in out
    assert "eligible" in out
    assert "tc_b" in out
    assert "Year 6" in out


def test_tests_list_empty_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.list_rulebook_test_cases.return_value = {"test_cases": []}
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "tests", "list", "rb_x"])
    assert result.exit_code == 0
    assert "No test cases yet" in _strip(result.output)


def test_tests_delete_with_yes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.delete_rulebook_test_case.return_value = None
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _runner_invoke(["rulebooks", "tests", "delete", "rb_x", "tc_abc", "--yes"])
    assert result.exit_code == 0, result.output
    client.delete_rulebook_test_case.assert_called_once_with("rb_x", "tc_abc")


# ---------------------------------------------------------------------------
# rulebooks list — anonymous fallthrough (no API key)
# ---------------------------------------------------------------------------


def _patch_anonymous(rulebooks):
    """Patch the no-key path: resolver returns None, anonymous client returns
    the given catalogue, lazy-auth client construction is observable."""
    anon_client = MagicMock()
    anon_client.__enter__ = MagicMock(return_value=anon_client)
    anon_client.__exit__ = MagicMock(return_value=False)
    anon_client.list_public_rulebooks.return_value = rulebooks
    return anon_client


def test_rulebooks_list_no_key_falls_through_to_public_catalogue(tmp_path, monkeypatch):
    """A fresh user with no key gets the anonymous public catalogue —
    never a browser sign-in prompt — from a read-only browse command."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.setenv("COLUMNS", "200")

    anon_client = _patch_anonymous(
        [
            {
                "rulebook_id": "rb_pub",
                "slug": "aethis/uk-fsm",
                "name": "UK Free School Meals",
                "domain": "uk_fsm",
                "status": "active",
                "visibility": "public",
                "ruleset_refs": [],
            }
        ]
    )
    with (
        patch("aethis_cli.commands.rulebooks_cmd.resolve_cached_key", return_value=None),
        patch("aethis_cli.commands.rulebooks_cmd.make_anonymous_client", return_value=anon_client),
        patch("aethis_cli.commands.rulebooks_cmd.load_client_or_fallback") as load_client,
    ):
        result = _runner_invoke(["rulebooks", "list"])

    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    assert "showing public rulebooks" in out
    assert "aethis/uk-fsm" in out
    # The lazy-auth client (and therefore the browser login hook) must never
    # be constructed on this path.
    load_client.assert_not_called()


def test_rulebooks_list_no_key_empty_catalogue_points_at_rulesets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.setenv("COLUMNS", "200")

    anon_client = _patch_anonymous([])
    with (
        patch("aethis_cli.commands.rulebooks_cmd.resolve_cached_key", return_value=None),
        patch("aethis_cli.commands.rulebooks_cmd.make_anonymous_client", return_value=anon_client),
        patch("aethis_cli.commands.rulebooks_cmd.load_client_or_fallback") as load_client,
    ):
        result = _runner_invoke(["rulebooks", "list"])

    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    assert "No public rulebooks published yet" in out
    assert "aethis rulesets list" in out
    load_client.assert_not_called()


def test_rulebooks_list_with_cached_key_lists_normally(tmp_path, monkeypatch):
    """A cached key (no env var) goes straight to the normal listing path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.setenv("COLUMNS", "200")

    client = MagicMock()
    client.list_rulebooks.return_value = []
    with (
        patch("aethis_cli.commands.rulebooks_cmd.resolve_cached_key", return_value="ak_cached"),
        patch(
            "aethis_cli.commands.rulebooks_cmd.load_client_or_fallback",
            return_value=(MagicMock(), client),
        ),
        patch(
            "aethis_cli.commands.rulebooks_cmd.make_anonymous_client",
            return_value=_patch_anonymous([]),
        ),
    ):
        result = _runner_invoke(["rulebooks", "list"])

    assert result.exit_code == 0
    assert "No rulebooks yet" in _strip(result.output)
