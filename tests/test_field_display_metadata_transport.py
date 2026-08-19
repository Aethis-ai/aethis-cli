"""Authored display metadata reaches the engine on the field pin.

Two optional per-field properties ride the upload:

* ``enum_labels`` — the wording a human should see for each member slug, so a
  consumer renders "United Kingdom" where the decision records ``united_kingdom``;
* ``canonical_field`` — the storage key the field's value belongs to, so the
  pairing is authored once beside the field rather than re-derived downstream.

The obligations covered here: both are transmitted when declared, both are
omitted cleanly when absent (an older project's payload is unchanged, asserted
by exact equality, not by absence of a substring), the local validation catches
the shapes the engine would accept and never render, and an engine that does
not model the properties is refused rather than allowed to swallow them.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import httpx
import pytest
import respx
import typer

from aethis_cli.client import AethisClient

from aethis_cli.commands import generate_cmd

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(capsys) -> str:
    return re.sub(r"\s+", " ", _ANSI.sub("", capsys.readouterr().out))


LABELLED_FIELDS = """\
fields:
  - key: craft.propulsion
    type: enum
    enum_values:
      - ion_drive
      - chemical
    enum_labels:
      ion_drive: Ion drive
      chemical: Chemical
    canonical_field: spacecraft.propulsion
  - key: craft.dry_mass_kg
    type: int
"""

PLAIN_FIELDS = """\
fields:
  - key: craft.propulsion
    type: enum
    enum_values:
      - ion_drive
      - chemical
  - key: craft.dry_mass_kg
    type: int
"""

ENGINE_WITH_SUPPORT = {"key", "sort", "enum_values", "value_space", "enum_labels", "canonical_field"}
ENGINE_WITHOUT_SUPPORT = {"key", "sort", "enum_values"}


def _project(tmp_path, body: str):
    path = tmp_path / "fields" / "fields.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return tmp_path


def _client(properties=ENGINE_WITH_SUPPORT) -> MagicMock:
    client = MagicMock()
    client.expected_field_spec_properties.return_value = properties
    client.base_url = "https://staging.api.aethis.ai"
    return client


# --- transmission -----------------------------------------------------------


def test_upload_transmits_labels_and_canonical_field(tmp_path):
    client = _client()

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, LABELLED_FIELDS))

    _, expected_fields = client.set_field_spec.call_args.args
    spec = next(f for f in expected_fields if f["key"] == "craft.propulsion")
    assert spec["enum_labels"] == {"ion_drive": "Ion drive", "chemical": "Chemical"}
    assert spec["canonical_field"] == "spacecraft.propulsion"


def test_payload_is_unchanged_for_a_project_that_declares_neither(tmp_path):
    """The exact payload an older project produced, asserted whole.

    Equality, not "``enum_labels`` not in the dict": a containment check passes
    against a payload that has grown any other key too, and the promise here is
    that nothing about an unmigrated project's upload moved.
    """
    client = _client()

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, PLAIN_FIELDS))

    _, expected_fields = client.set_field_spec.call_args.args
    assert expected_fields == [
        {"key": "craft.propulsion", "sort": "enum", "enum_values": ["ion_drive", "chemical"]},
        {"key": "craft.dry_mass_kg", "sort": "int"},
    ]


def test_a_field_without_metadata_stays_bare_alongside_one_that_has_it(tmp_path):
    """Omission is per field, not per project."""
    client = _client()

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, LABELLED_FIELDS))

    _, expected_fields = client.set_field_spec.call_args.args
    assert next(f for f in expected_fields if f["key"] == "craft.dry_mass_kg") == {
        "key": "craft.dry_mass_kg",
        "sort": "int",
    }


# --- validation -------------------------------------------------------------


def test_validate_accepts_labels_and_canonical_field():
    errors = generate_cmd.validate_fields_list(
        [
            {
                "key": "craft.propulsion",
                "type": "enum",
                "enum_values": ["ion_drive"],
                "enum_labels": {"ion_drive": "Ion drive"},
                "canonical_field": "spacecraft.propulsion",
            }
        ]
    )
    assert errors == []


def test_validate_accepts_labels_on_a_referenced_enum():
    """Members live on the registry, so only the shape can be checked here."""
    errors = generate_cmd.validate_fields_list(
        [
            {
                "key": "craft.origin",
                "type": "enum",
                "value_space": "space/agencies",
                "enum_labels": {"esa": "European Space Agency"},
            }
        ]
    )
    assert errors == []


def test_validate_rejects_a_label_for_an_undeclared_member():
    errors = generate_cmd.validate_fields_list(
        [
            {
                "key": "craft.propulsion",
                "type": "enum",
                "enum_values": ["ion_drive"],
                "enum_labels": {"ion_drive": "Ion drive", "solar_sail": "Solar sail"},
            }
        ]
    )
    assert any("solar_sail" in e for e in errors)


def test_validate_rejects_labels_on_a_non_enum_field():
    errors = generate_cmd.validate_fields_list([{"key": "craft.dry_mass_kg", "type": "int", "enum_labels": {"x": "X"}}])
    assert any("enum_labels" in e and "int" in e for e in errors)


def test_validate_rejects_labels_that_are_not_a_mapping():
    errors = generate_cmd.validate_fields_list(
        [{"key": "craft.propulsion", "type": "enum", "enum_values": ["ion_drive"], "enum_labels": ["Ion drive"]}]
    )
    assert any("not a mapping" in e for e in errors)


def test_validate_rejects_an_empty_label():
    errors = generate_cmd.validate_fields_list(
        [
            {
                "key": "craft.propulsion",
                "type": "enum",
                "enum_values": ["ion_drive"],
                "enum_labels": {"ion_drive": "   "},
            }
        ]
    )
    assert any("empty or non-text" in e for e in errors)


def test_validate_rejects_an_empty_canonical_field():
    errors = generate_cmd.validate_fields_list([{"key": "craft.dry_mass_kg", "type": "int", "canonical_field": ""}])
    assert any("canonical_field" in e for e in errors)


# --- engine capability ------------------------------------------------------


def test_upload_aborts_when_the_engine_does_not_model_the_properties(tmp_path, capsys):
    client = _client(ENGINE_WITHOUT_SUPPORT)

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, LABELLED_FIELDS))

    client.set_field_spec.assert_not_called()
    out = _flat(capsys)
    assert "does not carry canonical_field, enum_labels" in out
    assert "Stopping before the field spec push" in out


def test_upload_proceeds_but_says_so_when_the_engine_schema_is_unreadable(tmp_path, capsys):
    """Unknown is not unsupported — refusing here would block a working upload."""
    client = _client(None)

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, LABELLED_FIELDS))

    client.set_field_spec.assert_called_once()
    assert "Could not read the engine's field-spec schema" in _flat(capsys)


def test_an_old_engine_is_not_probed_for_a_project_that_declares_nothing(tmp_path):
    """A project with no metadata must keep working against any engine."""
    client = _client(ENGINE_WITHOUT_SUPPORT)

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, PLAIN_FIELDS))

    client.expected_field_spec_properties.assert_not_called()
    client.set_field_spec.assert_called_once()


def test_the_probe_names_only_the_property_the_engine_is_missing(tmp_path, capsys):
    client = _client(ENGINE_WITHOUT_SUPPORT | {"enum_labels"})

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, LABELLED_FIELDS))

    # The whole clause, not "canonical_field appears somewhere": a guard that
    # named every gated key regardless would satisfy a containment check.
    assert "does not carry canonical_field on a field spec" in _flat(capsys)


# --- round-trip on disk -----------------------------------------------------


def test_fields_yaml_write_back_preserves_the_metadata():
    """``aethis fields pull`` merges server truth over the local entry; the
    authored metadata is local truth and must survive it — in its modelled
    place, which is why the fixture carries a key that sorts after it. A field
    with nothing following the metadata cannot tell a modelled key from one
    swept up by the trailing preserve-anything-we-do-not-know loop.
    """
    written = generate_cmd._field_to_yaml_dict(
        {
            "key": "craft.propulsion",
            "type": "enum",
            "enum_values": ["ion_drive"],
            "enum_labels": {"ion_drive": "Ion drive"},
            "canonical_field": "spacecraft.propulsion",
            "hints": ["Ask which drive the craft flies."],
        }
    )
    assert written["enum_labels"] == {"ion_drive": "Ion drive"}
    assert written["canonical_field"] == "spacecraft.propulsion"
    assert list(written) == ["key", "type", "enum_values", "enum_labels", "canonical_field", "hints"]


# --- the capability probe itself --------------------------------------------

BASE = "https://engine.example"


def _openapi(properties: list[str]) -> dict:
    return {"components": {"schemas": {"ExpectedFieldSpec": {"properties": {p: {} for p in properties}}}}}


@respx.mock(base_url=BASE)
def test_probe_reports_the_properties_the_engine_advertises(respx_mock):
    respx_mock.get("/openapi.json").mock(
        return_value=httpx.Response(200, json=_openapi(["key", "sort", "enum_labels"]))
    )
    with AethisClient("ak", BASE) as client:
        assert client.expected_field_spec_properties() == {"key", "sort", "enum_labels"}


@respx.mock(base_url=BASE)
def test_probe_is_asked_once_per_client(respx_mock):
    route = respx_mock.get("/openapi.json").mock(return_value=httpx.Response(200, json=_openapi(["key"])))
    with AethisClient("ak", BASE) as client:
        client.expected_field_spec_properties()
        client.expected_field_spec_properties()
    assert route.call_count == 1


@respx.mock(base_url=BASE)
def test_an_unreadable_schema_is_unknown_not_unsupported(respx_mock):
    respx_mock.get("/openapi.json").mock(return_value=httpx.Response(500))
    with AethisClient("ak", BASE) as client:
        assert client.expected_field_spec_properties() is None


@respx.mock(base_url=BASE)
def test_a_schema_without_the_model_at_all_is_unknown_rather_than_a_crash(respx_mock):
    respx_mock.get("/openapi.json").mock(return_value=httpx.Response(200, json={"components": {"schemas": {}}}))
    with AethisClient("ak", BASE) as client:
        assert client.expected_field_spec_properties() is None
