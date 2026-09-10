"""Authored field behaviour reaches the engine on the deterministic pin (#118)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import typer

from aethis_cli.commands import generate_cmd


SUPPORTED = {
    "key",
    "sort",
    "enum_values",
    "value_space",
    "enum_labels",
    "canonical_field",
    "label",
    "question",
    "weight",
    "elicitation_owner",
    "injection_source",
    "injection_phase",
    "recoverable_from",
    "x_ui_widget",
    "notes",
}


def _project(tmp_path, body: str):
    fields = tmp_path / "fields" / "fields.yaml"
    fields.parent.mkdir(parents=True)
    fields.write_text(body)
    return tmp_path


def _client(properties=SUPPORTED):
    client = MagicMock()
    client.expected_field_spec_properties.return_value = properties
    client.base_url = "https://engine.example"
    return client


def test_non_applicant_field_transmits_explicit_no_question_and_metadata(tmp_path):
    client = _client()
    project = _project(
        tmp_path,
        """\
fields:
  - key: case.review_date
    type: date
    label: Review date
    weight: 17
    elicitation_owner: caseworker
    injection_source: supervising_adviser
    injection_phase: pre_submission
    recoverable_from: [case_plan]
    x_ui_widget: free_text
""",
    )

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    _, expected_fields = client.set_field_spec.call_args.args
    assert expected_fields == [
        {
            "key": "case.review_date",
            "sort": "date",
            "label": "Review date",
            "weight": 17,
            "elicitation_owner": "caseworker",
            "injection_source": "supervising_adviser",
            "injection_phase": "pre_submission",
            "recoverable_from": ["case_plan"],
            "x_ui_widget": "free_text",
            "question": None,
        }
    ]


def test_applicant_question_is_transmitted_exactly(tmp_path):
    client = _client()
    project = _project(
        tmp_path,
        """\
fields:
  - key: person.has_degree
    type: bool
    label: Degree held
    question: Do you have a degree?
    elicitation_owner: applicant
""",
    )

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    _, expected_fields = client.set_field_spec.call_args.args
    assert expected_fields == [
        {
            "key": "person.has_degree",
            "sort": "bool",
            "label": "Degree held",
            "question": "Do you have a degree?",
            "elicitation_owner": "applicant",
        }
    ]


def test_legacy_field_payload_is_unchanged(tmp_path):
    client = _client()
    project = _project(
        tmp_path,
        """\
fields:
  - key: legacy.answer
    type: string
""",
    )

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    _, expected_fields = client.set_field_spec.call_args.args
    assert expected_fields == [{"key": "legacy.answer", "sort": "string"}]
    client.expected_field_spec_properties.assert_not_called()


def test_notes_are_normalised_and_transmitted_as_an_authoritative_pin(tmp_path):
    client = _client()
    project = _project(
        tmp_path,
        """\
fields:
  - key: example.confirmed
    type: bool
    notes:
      - note_text: Confirm the supplied record.
      - note_text: Displayed only to reviewers.
        source: example-policy#1
        metadata:
          type: rationale
          nested: {approved: true, count: 2}
""",
    )

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    _, expected_fields = client.set_field_spec.call_args.args
    assert expected_fields == [
        {
            "key": "example.confirmed",
            "sort": "bool",
            "notes": [
                {"note_text": "Confirm the supplied record.", "source": "", "metadata": {}},
                {
                    "note_text": "Displayed only to reviewers.",
                    "source": "example-policy#1",
                    "metadata": {"type": "rationale", "nested": {"approved": True, "count": 2}},
                },
            ],
        }
    ]
    client.expected_field_spec_properties.assert_called_once()


def test_empty_notes_transmits_authoritative_clear(tmp_path):
    client = _client()
    project = _project(tmp_path, "fields:\n  - key: example.confirmed\n    type: bool\n    notes: []\n")

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    _, expected_fields = client.set_field_spec.call_args.args
    assert expected_fields == [{"key": "example.confirmed", "sort": "bool", "notes": []}]
    client.expected_field_spec_properties.assert_called_once()


def test_missing_engine_capability_refuses_before_field_push(tmp_path):
    client = _client(SUPPORTED - {"elicitation_owner", "question"})
    project = _project(
        tmp_path,
        """\
fields:
  - key: case.review_date
    type: date
    elicitation_owner: caseworker
""",
    )

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    client.set_field_spec.assert_not_called()


def test_missing_notes_capability_refuses_before_field_push(tmp_path):
    client = _client(SUPPORTED - {"notes"})
    project = _project(
        tmp_path,
        """\
fields:
  - key: example.confirmed
    type: bool
    notes:
      - note_text: Confirm the supplied record.
""",
    )

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    client.set_field_spec.assert_not_called()


@pytest.mark.parametrize(
    "properties, notes",
    [
        (None, "[]"),
        (None, "- note_text: Confirm the supplied record."),
        (SUPPORTED - {"notes"}, "[]"),
        (SUPPORTED - {"notes"}, "- note_text: Confirm the supplied record."),
    ],
)
def test_declared_notes_abort_before_all_related_writes_when_support_is_not_proven(tmp_path, properties, notes):
    client = _client(properties)
    project = _project(
        tmp_path,
        f"""\
fields:
  - key: example.confirmed
    type: enum
    value_space: example/values
    notes:
      {notes}
""",
    )

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    client.set_field_spec.assert_not_called()
    client.add_guidance.assert_not_called()
    client.put_value_space.assert_not_called()
    client.get_value_space.assert_not_called()
