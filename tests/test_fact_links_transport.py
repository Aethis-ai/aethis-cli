"""An authored ``fact_links`` field key reaches the engine.

``fact_links`` is a project field-pin property (``ExpectedFieldSpec``): a list of
``{relation, field, why?}`` entries relating one field to another. The CLI
treats it as opaque, exactly like ``options_by``: it sends it as authored, and
refuses the push when the engine does not advertise the property, because such
an engine would accept the upload and drop the links.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import typer

from aethis_cli.commands import generate_cmd

PIN_BASE = {"key", "sort", "enum_values", "value_space", "enum_labels", "canonical_field"}
PIN_WITH_FACT_LINKS = PIN_BASE | {"fact_links"}

FACT_LINKS = [
    {
        "relation": "same_value",
        "field": "second.is_related_to_first",
        "why": "Both fields record the same fact, so the answers must agree.",
    }
]

LINKED_FIELDS = """\
fields:
  - key: first.is_related_to_second
    type: bool
    fact_links:
      - relation: same_value
        field: second.is_related_to_first
        why: "Both fields record the same fact, so the answers must agree."
  - key: second.is_related_to_first
    type: bool
"""

PLAIN_FIELDS = """\
fields:
  - key: first.is_related_to_second
    type: bool
  - key: second.is_related_to_first
    type: bool
"""


def _project(tmp_path, body: str):
    path = tmp_path / "fields" / "fields.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(body)
    return tmp_path


def _client(properties) -> MagicMock:
    client = MagicMock()
    client.expected_field_spec_properties.return_value = properties
    client.base_url = "https://engine.example"
    return client


def _pushed(client) -> list[dict]:
    _, expected_fields = client.set_field_spec.call_args.args
    return expected_fields


def test_fact_links_are_projected_onto_the_pin(tmp_path):
    client = _client(PIN_WITH_FACT_LINKS)

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, LINKED_FIELDS))

    assert _pushed(client) == [
        {"key": "first.is_related_to_second", "sort": "bool", "fact_links": FACT_LINKS},
        {"key": "second.is_related_to_first", "sort": "bool"},
    ]


def test_fact_links_against_an_engine_without_them_is_refused_before_the_push(tmp_path, capsys):
    client = _client(PIN_BASE)

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, LINKED_FIELDS))

    client.set_field_spec.assert_not_called()
    client.add_guidance.assert_not_called()
    assert "fact_links" in " ".join(capsys.readouterr().out.split())


def test_absent_fact_links_leave_the_pin_unchanged_and_never_probe(tmp_path):
    client = _client(PIN_BASE)

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, PLAIN_FIELDS))

    assert _pushed(client) == [
        {"key": "first.is_related_to_second", "sort": "bool"},
        {"key": "second.is_related_to_first", "sort": "bool"},
    ]
    client.expected_field_spec_properties.assert_not_called()


def test_fact_links_are_written_back_in_their_place():
    field = {
        "key": "first.is_related_to_second",
        "type": "bool",
        "route_met_label": "Met",
        "custom": 1,
        "fact_links": FACT_LINKS,
    }

    out = generate_cmd._field_to_yaml_dict(field)

    assert out["fact_links"] == FACT_LINKS
    keys = list(out)
    assert keys[keys.index("route_met_label") + 1] == "fact_links"
