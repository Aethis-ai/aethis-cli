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

import pathlib
import re
import tempfile
from unittest.mock import MagicMock

import httpx
import pytest
import respx
import typer
import yaml

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
    # `.get`, not subscript: a payload that dropped the key should fail on the
    # assertion below, saying what was expected and what arrived, rather than
    # raising KeyError — a red that reports the fixture, not the finding.
    assert spec.get("enum_labels") == {"ion_drive": "Ion drive", "chemical": "Chemical"}
    assert spec.get("canonical_field") == "spacecraft.propulsion"


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
    assert "Stopping before the push" in out


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


# --- the second upload path: rulebooks set-fields ---------------------------
#
# `aethis rulebooks set-fields` posts the fields file as authored, so it already
# TRANSMITS both properties with no code change. What it lacked was the guard:
# against an engine that models neither, the call returns 200 and the authored
# values are gone. It asks about RulebookFieldSpec, which is a different model
# from the project pin's ExpectedFieldSpec and can disagree with it on the same
# engine — prod carries the properties on neither, staging on both.

RULEBOOK_FIELDS_WITH_METADATA = [
    {
        "key": "craft.propulsion",
        "sort": "Enum",
        "enum_values": ["ion_drive", "chemical"],
        "enum_labels": {"ion_drive": "Ion drive"},
        "canonical_field": "spacecraft.propulsion",
    }
]
RULEBOOK_FIELDS_PLAIN = [{"key": "craft.dry_mass_kg", "sort": "Int"}]


def _rulebook_client(properties) -> MagicMock:
    client = MagicMock()
    client.rulebook_field_spec_properties.return_value = properties
    # The project-pin model must never be consulted by this path: an engine can
    # carry the properties on one model and not the other.
    client.expected_field_spec_properties.return_value = ENGINE_WITH_SUPPORT
    client.base_url = "https://staging.api.aethis.ai"
    return client


def test_set_fields_refuses_an_engine_that_would_drop_the_metadata(capsys):
    client = _rulebook_client(ENGINE_WITHOUT_SUPPORT)

    with pytest.raises(typer.Exit):
        generate_cmd.check_display_metadata_support(client, RULEBOOK_FIELDS_WITH_METADATA, rulebook=True)

    assert "does not carry canonical_field, enum_labels" in _flat(capsys)


def test_set_fields_proceeds_against_an_engine_that_carries_them():
    client = _rulebook_client(ENGINE_WITH_SUPPORT)

    generate_cmd.check_display_metadata_support(client, RULEBOOK_FIELDS_WITH_METADATA, rulebook=True)


def test_set_fields_is_unaffected_when_the_file_declares_nothing():
    """An existing rulebook fields file keeps working against any engine."""
    client = _rulebook_client(ENGINE_WITHOUT_SUPPORT)

    generate_cmd.check_display_metadata_support(client, RULEBOOK_FIELDS_PLAIN, rulebook=True)

    client.rulebook_field_spec_properties.assert_not_called()


def test_set_fields_does_not_gate_on_project_only_behaviour_metadata():
    """Project pins and rulebook vocabulary advertise different models."""
    client = _rulebook_client(ENGINE_WITHOUT_SUPPORT)
    fields = [
        {
            "key": "case.review_date",
            "sort": "Date",
            "elicitation_owner": "caseworker",
            "injection_source": "adviser_selected",
            "injection_phase": "pre_submission",
        }
    ]

    generate_cmd.check_display_metadata_support(client, fields, rulebook=True)

    client.rulebook_field_spec_properties.assert_not_called()


def test_set_fields_asks_about_the_rulebook_model_not_the_project_one():
    """The two models are distinct and can disagree on one engine, so asking
    the wrong one would clear a push the engine will not honour."""
    client = _rulebook_client(ENGINE_WITHOUT_SUPPORT)

    with pytest.raises(typer.Exit):
        generate_cmd.check_display_metadata_support(client, RULEBOOK_FIELDS_WITH_METADATA, rulebook=True)

    client.rulebook_field_spec_properties.assert_called_once()
    client.expected_field_spec_properties.assert_not_called()


def test_generation_path_asks_about_the_project_model_not_the_rulebook_one(tmp_path):
    client = _client()
    client.rulebook_field_spec_properties.return_value = ENGINE_WITHOUT_SUPPORT

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, LABELLED_FIELDS))

    client.expected_field_spec_properties.assert_called_once()
    client.rulebook_field_spec_properties.assert_not_called()


def test_set_fields_calls_the_guard_before_it_posts(monkeypatch):
    """Ordering is the whole point — a guard that ran after the POST would be
    refusing an upload the engine had already accepted and discarded."""
    from aethis_cli.commands import rulebooks_cmd

    calls: list[str] = []
    client = _rulebook_client(ENGINE_WITHOUT_SUPPORT)

    def record_post(*_args, **_kwargs):
        calls.append("post")
        # A realistic return value, so that removing the guard fails on the
        # assertion below rather than crashing on a bare mock — otherwise the
        # test is red for the fixture's shape, not for the missing guard.
        return {"fields": RULEBOOK_FIELDS_WITH_METADATA, "field_lock_state": "unlocked"}

    client.set_rulebook_fields.side_effect = record_post

    def refusing_guard(*_args, **_kwargs):
        calls.append("guard")
        raise typer.Exit(code=1)

    monkeypatch.setattr(rulebooks_cmd, "check_display_metadata_support", refusing_guard)
    monkeypatch.setattr(rulebooks_cmd, "load_client_or_fallback", lambda: (None, client))

    path = pathlib.Path(tempfile.mkdtemp()) / "fields.yaml"
    path.write_text(yaml.safe_dump({"fields": RULEBOOK_FIELDS_WITH_METADATA}))

    with pytest.raises(typer.Exit):
        rulebooks_cmd.set_fields("rb_1", path)

    assert calls == ["guard"], "the guard must run before the POST, and abort it"


def test_set_fields_refuses_for_real_against_an_engine_missing_the_rulebook_model(monkeypatch):
    """The command itself, guard and all — not the guard called directly.

    The client here carries the properties on the PROJECT pin model and not on
    the rulebook one, which is the state that separates a call site asking
    about what it posts from one asking about the other model and being wrongly
    cleared.
    """
    from aethis_cli.commands import rulebooks_cmd

    client = _rulebook_client(ENGINE_WITHOUT_SUPPORT)
    monkeypatch.setattr(rulebooks_cmd, "load_client_or_fallback", lambda: (None, client))

    path = pathlib.Path(tempfile.mkdtemp()) / "fields.yaml"
    path.write_text(yaml.safe_dump({"fields": RULEBOOK_FIELDS_WITH_METADATA}))

    with pytest.raises(typer.Exit):
        rulebooks_cmd.set_fields("rb_1", path)

    client.set_rulebook_fields.assert_not_called()


def test_set_fields_posts_for_real_when_the_file_declares_nothing(monkeypatch):
    """The unaffected half: an existing rulebook fields file still posts."""
    from aethis_cli.commands import rulebooks_cmd

    client = _rulebook_client(ENGINE_WITHOUT_SUPPORT)
    client.set_rulebook_fields.return_value = {"fields": RULEBOOK_FIELDS_PLAIN, "field_lock_state": "unlocked"}
    monkeypatch.setattr(rulebooks_cmd, "load_client_or_fallback", lambda: (None, client))

    path = pathlib.Path(tempfile.mkdtemp()) / "fields.yaml"
    path.write_text(yaml.safe_dump({"fields": RULEBOOK_FIELDS_PLAIN}))

    rulebooks_cmd.set_fields("rb_1", path)

    client.set_rulebook_fields.assert_called_once()


# --- presence, not truthiness -----------------------------------------------
#
# The engine's model makes both properties nullable and KEEPS an empty map:
# aethis-core asserts `field["enum_labels"] == {}` survives ingestion, and a
# live 0.56.0 staging engine returns 200 for `{}`, `null`, and `""`. So `{}` is
# a declaration, distinct from the property being absent, and the CLI may not
# collapse the two — a truthiness test drops exactly the shape the engine went
# to the trouble of preserving, and (because the guard keys off what reached
# the payload) silently skips the capability probe with it.

EMPTY_MAP_FIELDS = """\
fields:
  - key: craft.propulsion
    type: enum
    enum_values:
      - ion_drive
    enum_labels: {}
"""

NULL_METADATA_FIELDS = """\
fields:
  - key: craft.propulsion
    type: enum
    enum_values:
      - ion_drive
    enum_labels: null
  - key: craft.dry_mass_kg
    type: int
    canonical_field: null
"""


def test_an_empty_enum_labels_map_is_transmitted_as_declared(tmp_path):
    client = _client()

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, EMPTY_MAP_FIELDS))

    _, expected_fields = client.set_field_spec.call_args.args
    assert expected_fields == [
        {"key": "craft.propulsion", "sort": "enum", "enum_values": ["ion_drive"], "enum_labels": {}}
    ]


def test_an_empty_enum_labels_map_still_fires_the_capability_guard(tmp_path):
    """The bug this pairs with: a declared `{}` reached the engine's door with
    zero probes, so an engine that drops it was never questioned."""
    client = _client(ENGINE_WITHOUT_SUPPORT)

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, EMPTY_MAP_FIELDS))

    client.expected_field_spec_properties.assert_called_once()
    client.set_field_spec.assert_not_called()


def test_explicit_nulls_are_transmitted_as_declared(tmp_path):
    """Both properties are nullable on the engine's model and a live engine
    accepts null, so an explicit null is authored intent, not a typo to drop."""
    client = _client()

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, NULL_METADATA_FIELDS))

    _, expected_fields = client.set_field_spec.call_args.args
    assert expected_fields == [
        {"key": "craft.propulsion", "sort": "enum", "enum_values": ["ion_drive"], "enum_labels": None},
        {"key": "craft.dry_mass_kg", "sort": "int", "canonical_field": None},
    ]


def test_explicit_nulls_fire_the_capability_guard(tmp_path):
    client = _client(ENGINE_WITHOUT_SUPPORT)

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, NULL_METADATA_FIELDS))

    client.set_field_spec.assert_not_called()


def test_validation_accepts_an_empty_map_and_explicit_nulls():
    assert (
        generate_cmd.validate_fields_list(
            [
                {"key": "craft.propulsion", "type": "enum", "enum_values": ["ion_drive"], "enum_labels": {}},
                {"key": "craft.origin", "type": "enum", "enum_values": ["esa"], "enum_labels": None},
                {"key": "craft.dry_mass_kg", "type": "int", "canonical_field": None},
            ]
        )
        == []
    )


def test_an_empty_map_on_a_non_enum_field_is_still_rejected():
    """Presence-aware does not mean unchecked — `{}` is a declaration, and a
    declaration on a field with no members is still wrong."""
    errors = generate_cmd.validate_fields_list([{"key": "craft.dry_mass_kg", "type": "int", "enum_labels": {}}])
    assert any("enum_labels" in e and "int" in e for e in errors)


def test_non_text_label_keys_are_a_validation_message_not_a_crash():
    """A YAML author writing `2024: something` gets an int key. Formatting the
    error used to join it into a string and raise."""
    errors = generate_cmd.validate_fields_list(
        [{"key": "craft.propulsion", "type": "enum", "enum_values": ["ion_drive"], "enum_labels": {2024: "Ion drive"}}]
    )
    assert any("non-text enum_labels member" in e for e in errors)


def test_write_back_preserves_an_explicit_empty_map():
    """A pull must not un-author a declared `{}` by calling it empty."""
    written = generate_cmd._field_to_yaml_dict(
        {"key": "craft.propulsion", "type": "enum", "enum_values": ["ion_drive"], "enum_labels": {}}
    )
    assert "enum_labels" in written
    assert written["enum_labels"] == {}


def test_write_back_still_omits_a_property_that_was_never_declared():
    written = generate_cmd._field_to_yaml_dict({"key": "craft.dry_mass_kg", "type": "int"})
    assert "enum_labels" not in written
    assert "canonical_field" not in written


def test_the_empty_canonical_field_message_discloses_the_stricture():
    """The CLI is stricter than the engine here, and the author is told so.

    A bare "empty or not text" reads as a format complaint and sends someone
    looking for the rule in the engine, where there isn't one. The message has
    to say the engine would take it, why the CLI will not, and what to write
    instead.
    """
    errors = generate_cmd.validate_fields_list([{"key": "craft.dry_mass_kg", "type": "int", "canonical_field": ""}])

    message = next(e for e in errors if "canonical_field" in e)
    assert "engine would accept it" in message
    assert "no consumer can resolve" in message
    assert "null" in message
