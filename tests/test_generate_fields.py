"""Tests for fields.yaml parsing + field-vocabulary upload in `aethis generate`."""

from __future__ import annotations

from unittest.mock import MagicMock

from aethis_cli.commands import generate_cmd


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
