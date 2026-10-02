"""Authored ``options_by``, ``input_role`` and ``identity_binding`` reach the engine.

``options_by`` is a project field-pin property (``ExpectedFieldSpec``): it rides
``aethis generate`` exactly as ``enum_labels`` does. ``input_role`` and
``identity_binding`` are rulebook vocabulary properties (``RulebookFieldSpec``):
``aethis rulebooks set-fields`` posts them as authored. On both paths an engine
that does not model the property would accept the upload and drop it, so the
push is refused first.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import typer
import yaml

from aethis_cli.commands import generate_cmd, rulebooks_cmd

PIN_BASE = {"key", "sort", "enum_values", "value_space", "enum_labels", "canonical_field"}
PIN_WITH_OPTIONS_BY = PIN_BASE | {"options_by"}

RULEBOOK_BASE = {"key", "sort", "enum_values", "enum_labels", "canonical_field"}
RULEBOOK_WITH_IDENTITY = RULEBOOK_BASE | {"input_role", "identity_binding"}

OPTIONS_BY = {
    "field": "vehicle.make",
    "map": {"ford": ["focus", "fiesta"], "tesla": ["model_3", "model_y"]},
}

DEPENDENT_FIELDS = """\
fields:
  - key: vehicle.make
    type: enum
    enum_values: [ford, tesla]
  - key: vehicle.model
    type: enum
    enum_values: [focus, fiesta, model_3, model_y]
    options_by:
      field: vehicle.make
      map:
        ford: [focus, fiesta]
        tesla: [model_3, model_y]
"""

PLAIN_FIELDS = """\
fields:
  - key: vehicle.make
    type: enum
    enum_values: [ford, tesla]
  - key: vehicle.model
    type: enum
    enum_values: [focus, fiesta, model_3, model_y]
"""

IDENTITY_FIELDS = [
    {"key": "owner.age", "sort": "Int"},
    {
        "key": "owner.given_names",
        "sort": "String",
        "input_role": "factual",
        "identity_binding": {"subject": "application_subject", "component": "given_names"},
    },
]


def _project(tmp_path, body: str):
    path = tmp_path / "fields" / "fields.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(body)
    return tmp_path


def _pin_client(properties) -> MagicMock:
    client = MagicMock()
    client.expected_field_spec_properties.return_value = properties
    client.rulebook_field_spec_properties.return_value = RULEBOOK_WITH_IDENTITY
    client.base_url = "https://engine.example"
    return client


def _rulebook_client(properties) -> MagicMock:
    client = MagicMock()
    client.rulebook_field_spec_properties.return_value = properties
    client.expected_field_spec_properties.return_value = PIN_WITH_OPTIONS_BY
    client.base_url = "https://engine.example"
    client.set_rulebook_fields.return_value = {"fields": IDENTITY_FIELDS, "field_lock_state": "unlocked"}
    return client


def _pushed(client) -> list[dict]:
    _, expected_fields = client.set_field_spec.call_args.args
    return expected_fields


# --- options_by on the generation pin ---------------------------------------


def test_options_by_is_projected_onto_the_pin(tmp_path):
    client = _pin_client(PIN_WITH_OPTIONS_BY)

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, DEPENDENT_FIELDS))

    assert _pushed(client) == [
        {"key": "vehicle.make", "sort": "enum", "enum_values": ["ford", "tesla"]},
        {
            "key": "vehicle.model",
            "sort": "enum",
            "enum_values": ["focus", "fiesta", "model_3", "model_y"],
            "options_by": OPTIONS_BY,
        },
    ]


def test_options_by_against_an_engine_without_it_is_refused_before_the_push(tmp_path, capsys):
    client = _pin_client(PIN_BASE)

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, DEPENDENT_FIELDS))

    client.set_field_spec.assert_not_called()
    client.add_guidance.assert_not_called()
    assert "options_by" in " ".join(capsys.readouterr().out.split())


def test_absent_options_by_leaves_the_pin_unchanged_and_never_probes(tmp_path):
    client = _pin_client(PIN_BASE)

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, PLAIN_FIELDS))

    assert _pushed(client) == [
        {"key": "vehicle.make", "sort": "enum", "enum_values": ["ford", "tesla"]},
        {"key": "vehicle.model", "sort": "enum", "enum_values": ["focus", "fiesta", "model_3", "model_y"]},
    ]
    client.expected_field_spec_properties.assert_not_called()


def test_options_by_survives_a_fields_yaml_round_trip():
    field = {"key": "vehicle.model", "type": "enum", "enum_values": ["focus"], "options_by": OPTIONS_BY}

    assert generate_cmd._field_to_yaml_dict(field)["options_by"] == OPTIONS_BY


def test_rulebook_identity_keys_are_not_projected_onto_the_pin(tmp_path):
    """input_role / identity_binding live on the rulebook model; the project pin
    does not carry them, so they neither ride the pin nor gate it."""
    client = _pin_client(PIN_BASE)
    project = _project(
        tmp_path,
        """\
fields:
  - key: owner.given_names
    type: string
    input_role: factual
    identity_binding: {subject: application_subject, component: given_names}
""",
    )

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    assert _pushed(client) == [{"key": "owner.given_names", "sort": "string"}]
    client.expected_field_spec_properties.assert_not_called()


# --- input_role / identity_binding on rulebooks set-fields -------------------


@pytest.mark.parametrize("missing", ["input_role", "identity_binding"])
def test_set_fields_refuses_an_engine_missing_an_identity_key(missing, tmp_path, monkeypatch, capsys):
    client = _rulebook_client(RULEBOOK_WITH_IDENTITY - {missing})
    monkeypatch.setattr(rulebooks_cmd, "load_client_or_fallback", lambda: (None, client))
    path = tmp_path / "fields.yaml"
    path.write_text(yaml.safe_dump({"fields": IDENTITY_FIELDS}))

    with pytest.raises(typer.Exit):
        rulebooks_cmd.set_fields("rb_1", path)

    client.set_rulebook_fields.assert_not_called()
    assert f"does not carry {missing}" in " ".join(capsys.readouterr().out.split())


def test_set_fields_posts_identity_keys_as_authored(tmp_path, monkeypatch):
    client = _rulebook_client(RULEBOOK_WITH_IDENTITY)
    monkeypatch.setattr(rulebooks_cmd, "load_client_or_fallback", lambda: (None, client))
    path = tmp_path / "fields.yaml"
    path.write_text(yaml.safe_dump({"fields": IDENTITY_FIELDS}))

    rulebooks_cmd.set_fields("rb_1", path)

    client.set_rulebook_fields.assert_called_once_with("rb_1", IDENTITY_FIELDS)


def test_set_fields_without_identity_keys_never_probes(tmp_path, monkeypatch):
    client = _rulebook_client(RULEBOOK_BASE)
    monkeypatch.setattr(rulebooks_cmd, "load_client_or_fallback", lambda: (None, client))
    plain = [{"key": "owner.age", "sort": "Int"}]
    path = tmp_path / "fields.yaml"
    path.write_text(yaml.safe_dump({"fields": plain}))

    rulebooks_cmd.set_fields("rb_1", path)

    client.set_rulebook_fields.assert_called_once_with("rb_1", plain)
    client.rulebook_field_spec_properties.assert_not_called()


def test_set_fields_does_not_gate_on_the_project_only_options_by():
    """options_by is a pin property; a shared authoring file carrying it must
    not make the rulebook command refuse an engine compatible with that route."""
    client = _rulebook_client(RULEBOOK_BASE)
    fields = [{"key": "vehicle.model", "sort": "Enum", "enum_values": ["focus"], "options_by": OPTIONS_BY}]

    generate_cmd.check_display_metadata_support(client, fields, rulebook=True)

    client.rulebook_field_spec_properties.assert_not_called()
