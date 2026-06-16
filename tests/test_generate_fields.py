"""Tests for fields.yaml parsing + field-vocabulary upload in `aethis generate`."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer

from aethis_cli.commands import generate_cmd
from aethis_cli.config import read_state, write_state


# --- helpers ---------------------------------------------------------------


def _write_fields(path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


RULESET_FIELDS = """\
fields:
  - key: applicant.income
    type: int
    question: "What is your annual income?"
  - key: applicant.date_of_birth
    type: date
    label: "Date of birth"
    hints:
      - "Why we ask: age determines the route."
"""

RULEBOOK_FIELDS = """\
fields:
  - key: applicant.date_of_birth
    type: date
    question: "Please give your date of birth."
"""


# --- pure parsing ----------------------------------------------------------


def test_parse_fields_yaml_keys(tmp_path):
    p = tmp_path / "fields" / "fields.yaml"
    _write_fields(p, RULESET_FIELDS)
    parsed = generate_cmd._parse_fields_yaml(p)
    assert list(parsed) == ["applicant.income", "applicant.date_of_birth"]


def test_field_guidance_lines_from_question_label_hints():
    lines = generate_cmd._field_guidance_lines(
        "applicant.date_of_birth",
        {"question": "DOB?", "label": "Date of birth", "hints": ["age matters"]},
    )
    joined = "\n".join(lines)
    assert "DOB?" in joined
    assert "Date of birth" in joined
    assert "age matters" in joined


def test_parent_rulebook_dir_detection(tmp_path):
    rb = tmp_path / "rb"
    (rb / "rulesets" / "child").mkdir(parents=True)
    (rb / "aethis.yaml").write_text("kind: rulebook\n")
    found = generate_cmd._parent_rulebook_dir(rb / "rulesets" / "child")
    assert found == rb
    # A standalone project has no parent rulebook.
    assert generate_cmd._parent_rulebook_dir(tmp_path / "solo") is None


# --- upload (rulebook-wins merge) ------------------------------------------


def test_upload_field_vocabulary_standalone(tmp_path):
    _write_fields(tmp_path / "fields" / "fields.yaml", RULESET_FIELDS)
    client = MagicMock()
    generate_cmd._upload_field_vocabulary(client, "proj_1", tmp_path)

    client.set_field_spec.assert_called_once()
    _, expected_fields = client.set_field_spec.call_args.args
    keys = {f["key"]: f["sort"] for f in expected_fields}
    assert keys == {"applicant.income": "int", "applicant.date_of_birth": "date"}


def test_upload_field_vocabulary_rulebook_wins(tmp_path):
    """A field defined at the rulebook level wins over the ruleset's own."""
    rb = tmp_path / "rb"
    child = rb / "rulesets" / "child"
    child.mkdir(parents=True)
    (rb / "aethis.yaml").write_text("kind: rulebook\n")
    _write_fields(rb / "fields" / "fields.yaml", RULEBOOK_FIELDS)
    _write_fields(child / "fields" / "fields.yaml", RULESET_FIELDS)

    client = MagicMock()
    generate_cmd._upload_field_vocabulary(client, "proj_1", child)

    _, expected_fields = client.set_field_spec.call_args.args
    keys = [f["key"] for f in expected_fields]
    # Shared key appears once; income from the ruleset still flows through.
    assert keys.count("applicant.date_of_birth") == 1
    assert "applicant.income" in keys

    # The rulebook's wording for the shared field is what gets pushed as guidance.
    guidance = " ".join(c.args[1] for c in client.add_guidance.call_args_list)
    assert "Please give your date of birth." in guidance


def test_upload_field_vocabulary_noop_when_absent(tmp_path):
    client = MagicMock()
    generate_cmd._upload_field_vocabulary(client, "proj_1", tmp_path)
    client.set_field_spec.assert_not_called()


# --- type normalisation ----------------------------------------------------


def test_normalise_field_type_folds_long_and_server_forms():
    assert generate_cmd._normalise_field_type("integer") == "int"
    assert generate_cmd._normalise_field_type("Boolean") == "bool"
    assert generate_cmd._normalise_field_type("ENUM") == "enum"
    assert generate_cmd._normalise_field_type("date") == "date"
    assert generate_cmd._normalise_field_type(None) == "string"


# --- validation ------------------------------------------------------------


def test_validate_fields_list_accepts_valid():
    fields = [
        {"key": "a.income", "type": "int"},
        {"key": "a.kind", "type": "enum", "enum_values": ["x", "y"]},
    ]
    assert generate_cmd.validate_fields_list(fields) == []


def test_validate_fields_list_flags_problems():
    fields = [
        {"key": "a.income", "type": "money"},  # invalid type
        {"key": "a.kind", "type": "enum"},  # enum without values
        {"key": "a.income", "type": "int"},  # duplicate key
        {"type": "string"},  # missing key
    ]
    errors = generate_cmd.validate_fields_list(fields)
    joined = " ".join(errors)
    assert "invalid type" in joined
    assert "enum_values" in joined
    assert "Duplicate" in joined
    assert "missing a 'key'" in joined


# --- round-trip write ------------------------------------------------------


def test_write_fields_yaml_round_trips(tmp_path):
    path = tmp_path / "fields" / "fields.yaml"
    field_map = {
        "a.income": {"key": "a.income", "type": "int", "question": "Income?"},
        "a.kind": {"key": "a.kind", "type": "enum", "enum_values": ["x"], "hints": ["why"]},
    }
    generate_cmd._write_fields_yaml(path, field_map)
    reparsed = generate_cmd._parse_fields_yaml(path)
    assert list(reparsed) == ["a.income", "a.kind"]
    assert reparsed["a.kind"]["enum_values"] == ["x"]


# --- explicit rulebook: key ------------------------------------------------


def test_parent_rulebook_dir_explicit_key_wins(tmp_path):
    rb = tmp_path / "books" / "rb"
    rb.mkdir(parents=True)
    (rb / "aethis.yaml").write_text("project: rb\nkind: rulebook\n")
    child = tmp_path / "elsewhere" / "child"
    child.mkdir(parents=True)
    (child / "aethis.yaml").write_text("project: child\nkind: ruleset\nrulebook: ../../books/rb\n")
    # Not under <rulebook>/rulesets/, so only the explicit key can link it.
    assert generate_cmd._parent_rulebook_dir(child) == rb.resolve()


# --- post-generate diff ----------------------------------------------------


# --- shared project/source helpers -----------------------------------------


def test_resolve_or_create_project_reuses_existing(tmp_path):
    client = MagicMock()
    cfg = SimpleNamespace(project_id="proj_9", project="p", config_path=tmp_path)
    assert generate_cmd._resolve_or_create_project(client, cfg) == "proj_9"
    client.create_project.assert_not_called()


def test_resolve_or_create_project_creates_and_resets_ledger(tmp_path):
    client = MagicMock()
    client.create_project.return_value = {"project_id": "proj_new"}
    cfg = SimpleNamespace(project_id=None, project="p", config_path=tmp_path)
    assert generate_cmd._resolve_or_create_project(client, cfg) == "proj_new"
    assert read_state(tmp_path).get("uploaded_sources") == {}  # fresh project → empty ledger


def test_upload_sources_is_idempotent(tmp_path):
    src = tmp_path / "sources"
    src.mkdir()
    (src / "a.md").write_text("hello")
    client = MagicMock()

    # First upload sends the file.
    assert generate_cmd._upload_sources(client, "proj_1", tmp_path) == 1
    assert client.upload_sources.call_count == 1

    # Unchanged → nothing re-uploaded (the discover→generate double-upload fix).
    assert generate_cmd._upload_sources(client, "proj_1", tmp_path) == 0
    assert client.upload_sources.call_count == 1

    # Editing the file (newer mtime) makes it upload again.
    (src / "a.md").write_text("changed")
    os.utime(src / "a.md", ns=(2_000_000_000, 2_000_000_000))
    assert generate_cmd._upload_sources(client, "proj_1", tmp_path) == 1
    assert client.upload_sources.call_count == 2


def test_safe_field_type_clamps_unknown_and_valueless_enum():
    assert generate_cmd._safe_field_type("money", None) == "string"  # unknown → safe default
    assert generate_cmd._safe_field_type("enum", None) == "string"  # enum needs values
    assert generate_cmd._safe_field_type("enum", ["a", "b"]) == "enum"
    assert generate_cmd._safe_field_type("integer", None) == "int"


def test_write_fields_yaml_preserves_unmodelled_keys(tmp_path):
    """A round-trip write keeps hand-authored keys we don't model."""
    path = tmp_path / "fields" / "fields.yaml"
    field_map = {"a.x": {"key": "a.x", "type": "int", "description": "keep me", "weight": 3}}
    generate_cmd._write_fields_yaml(path, field_map)
    written = generate_cmd._load_yaml_file(path)["fields"][0]
    assert written["description"] == "keep me"
    assert written["weight"] == 3


def test_upload_field_vocabulary_rejects_duplicate_keys(tmp_path):
    """Duplicate keys within a file fail fast before any server mutation."""
    _write_fields(
        tmp_path / "fields" / "fields.yaml",
        "fields:\n  - key: a.x\n    type: int\n  - key: a.x\n    type: bool\n",
    )
    client = MagicMock()
    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", tmp_path)
    client.set_field_spec.assert_not_called()


def test_poll_success_without_ruleset_id_keeps_prior_state(tmp_path, monkeypatch):
    """A success that never surfaces latest_ruleset_id must not clobber a prior id."""
    monkeypatch.setattr(generate_cmd.time, "sleep", lambda *_a, **_k: None)
    write_state(tmp_path, {"ruleset_id": "old:123"})
    client = MagicMock()
    client.get_status.return_value = {"job": {"status": "success", "progress_percent": 100}}
    generate_cmd._poll_until_done(client, "proj_1", tmp_path, timeout=30)
    assert read_state(tmp_path)["ruleset_id"] == "old:123"


def test_poll_success_records_ruleset_id(tmp_path, monkeypatch):
    monkeypatch.setattr(generate_cmd.time, "sleep", lambda *_a, **_k: None)
    client = MagicMock()
    client.get_status.return_value = {"job": {"status": "success"}, "latest_ruleset_id": "new:456"}
    generate_cmd._poll_until_done(client, "proj_1", tmp_path, timeout=30)
    assert read_state(tmp_path)["ruleset_id"] == "new:456"


def test_report_field_diff_flags_drift(tmp_path, capsys):
    _write_fields(tmp_path / "fields" / "fields.yaml", RULESET_FIELDS)
    client = MagicMock()
    client.get_schema.return_value = {
        "fields": [
            {"field_id": "applicant.income"},
            {"field_id": "applicant.surprise"},  # produced but not pinned
        ]
    }
    generate_cmd._report_field_diff(client, "rs_1", tmp_path)
    out = capsys.readouterr().out
    assert "applicant.date_of_birth" in out  # pinned but not produced
    assert "applicant.surprise" in out  # produced but not pinned
