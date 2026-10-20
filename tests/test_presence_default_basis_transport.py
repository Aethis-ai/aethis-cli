"""An authored ``presence_default_basis`` field key reaches the engine.

``presence_default_basis`` is a project field-pin property: ``{reason, source}``
saying why a field's unanswered reading is meant to be the generous one. The CLI
treats it as opaque, exactly like ``fact_links``: it sends it as authored, and
refuses the push when the engine does not advertise the property, because such
an engine would accept the upload and drop it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import typer

from aethis_cli.commands import generate_cmd

PIN_BASE = {"key", "sort", "enum_values", "value_space", "enum_labels", "canonical_field"}
PIN_WITH_BASIS = PIN_BASE | {"presence_default_basis"}

BASIS = {"reason": "Treated as met unless a reviewer records a concern.", "source": "handbook#4.2"}

DOCUMENTED_FIELDS = """\
fields:
  - key: review.no_concern_recorded
    type: bool
    presence_default_basis:
      reason: "Treated as met unless a reviewer records a concern."
      source: "handbook#4.2"
  - key: applicant.age
    type: int
"""

PLAIN_FIELDS = """\
fields:
  - key: review.no_concern_recorded
    type: bool
  - key: applicant.age
    type: int
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


def test_the_basis_is_projected_onto_the_pin(tmp_path):
    client = _client(PIN_WITH_BASIS)

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, DOCUMENTED_FIELDS))

    assert _pushed(client) == [
        {"key": "review.no_concern_recorded", "sort": "bool", "presence_default_basis": BASIS},
        {"key": "applicant.age", "sort": "int"},
    ]


def test_the_basis_against_an_engine_without_it_is_refused_before_the_push(tmp_path, capsys):
    client = _client(PIN_BASE)

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, DOCUMENTED_FIELDS))

    client.set_field_spec.assert_not_called()
    assert "presence_default_basis" in " ".join(capsys.readouterr().out.split())


def test_no_basis_leaves_the_pin_unchanged_and_never_probes(tmp_path):
    client = _client(PIN_BASE)

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, PLAIN_FIELDS))

    assert _pushed(client) == [
        {"key": "review.no_concern_recorded", "sort": "bool"},
        {"key": "applicant.age", "sort": "int"},
    ]
    client.expected_field_spec_properties.assert_not_called()


def test_the_basis_is_written_back_in_its_place():
    field = {"key": "review.no_concern_recorded", "type": "bool", "fact_links": [], "presence_default_basis": BASIS}

    out = generate_cmd._field_to_yaml_dict(field)

    assert out["presence_default_basis"] == BASIS
    keys = list(out)
    assert keys[keys.index("fact_links") + 1] == "presence_default_basis"
