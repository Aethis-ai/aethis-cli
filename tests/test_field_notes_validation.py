"""Authored field ``notes`` are validated locally, before any engine call (#135)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import typer

from aethis_cli.commands import generate_cmd


def _errors(notes):
    return generate_cmd.validate_fields_list([{"key": "applicant.fact", "type": "bool", "notes": notes}])


def test_omitted_notes_is_valid():
    assert generate_cmd.validate_fields_list([{"key": "applicant.fact", "type": "bool"}]) == []


def test_empty_list_is_a_valid_clear():
    assert _errors([]) == []


def test_valid_entry_with_all_keys():
    assert _errors([{"note_text": "x", "source": "s", "metadata": {"a": [1, 2.5, None, True, {"b": "c"}]}}]) == []


def test_text_only_entry_is_valid():
    assert _errors([{"note_text": "x"}]) == []


def test_null_notes_rejected_pointing_at_empty_list():
    errors = _errors(None)
    assert len(errors) == 1
    assert "applicant.fact" in errors[0] and "[]" in errors[0]


@pytest.mark.parametrize("bad", ["text", {"note_text": "x"}, 3])
def test_non_list_rejected(bad):
    errors = _errors(bad)
    assert len(errors) == 1 and "list" in errors[0]


@pytest.mark.parametrize("bad", ["text", 1, None, ["x"]])
def test_non_object_entry_rejected_with_index(bad):
    errors = _errors([{"note_text": "ok"}, bad])
    assert len(errors) == 1
    assert "applicant.fact" in errors[0] and "notes[1]" in errors[0]


@pytest.mark.parametrize("entry", [{}, {"source": "s"}, {"note_text": 5}, {"note_text": None}, {"note_text": ["a"]}])
def test_missing_or_non_string_note_text_rejected(entry):
    errors = _errors([entry])
    assert any("notes[0]" in e and "note_text" in e for e in errors)


@pytest.mark.parametrize("source", [5, None, ["a"], {"a": 1}])
def test_non_string_source_rejected(source):
    errors = _errors([{"note_text": "x", "source": source}])
    assert any("notes[0]" in e and "source" in e for e in errors)


@pytest.mark.parametrize("metadata", ["m", None, [1], 3])
def test_non_object_metadata_rejected(metadata):
    errors = _errors([{"note_text": "x", "metadata": metadata}])
    assert any("notes[0]" in e and "metadata" in e for e in errors)


def test_unknown_entry_keys_rejected():
    errors = _errors([{"note_text": "x", "extra": 1, "other": 2}])
    assert len(errors) == 1
    assert "notes[0]" in errors[0] and "extra" in errors[0] and "other" in errors[0]


@pytest.mark.parametrize(
    "metadata",
    [
        {"a": float("nan")},
        {"a": float("inf")},
        {"a": {"b": [1, float("-inf")]}},
        {1: "x"},
        {"a": {2: "x"}},
        {"a": {1, 2}},
        {"a": b"bytes"},
        {"a": object()},
        {"a": (1, 2)},
    ],
)
def test_non_json_metadata_rejected_recursively(metadata):
    errors = _errors([{"note_text": "x", "metadata": metadata}])
    assert any("notes[0]" in e and "metadata" in e for e in errors)


def test_boolean_note_text_rejected():
    assert _errors([{"note_text": True}])


def _project(tmp_path, body):
    fields = tmp_path / "fields" / "fields.yaml"
    fields.parent.mkdir(parents=True)
    fields.write_text(body)
    return tmp_path


def test_invalid_notes_refuse_before_any_engine_call(tmp_path):
    client = MagicMock()
    project = _project(
        tmp_path,
        """\
fields:
  - key: applicant.fact
    type: bool
    notes:
      - note_text: 5
""",
    )

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    assert client.method_calls == []


def test_empty_notes_list_is_sent_as_authoritative_clear(tmp_path):
    client = MagicMock()
    client.expected_field_spec_properties.return_value = {"key", "sort", "notes"}
    project = _project(tmp_path, "fields:\n  - key: applicant.fact\n    type: bool\n    notes: []\n")

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    _, expected_fields = client.set_field_spec.call_args.args
    assert expected_fields == [{"key": "applicant.fact", "sort": "bool", "notes": []}]


def test_omitted_notes_sends_no_notes_key(tmp_path):
    client = MagicMock()
    client.expected_field_spec_properties.return_value = {"key", "sort", "notes"}
    project = _project(tmp_path, "fields:\n  - key: applicant.fact\n    type: bool\n")

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    _, expected_fields = client.set_field_spec.call_args.args
    assert "notes" not in expected_fields[0]
