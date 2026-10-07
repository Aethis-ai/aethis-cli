"""Authored ``collection`` fields and ``computed`` declarations.

Two field declarations the engine models beyond the scalar sorts:

* a ``collection`` field — a multi-select over a closed member set, authored as
  ``type: collection`` with an ``items`` declaration (``sort``, ``enum_values``
  or a field-level ``value_space``, optionally ``max_items`` and
  ``completion_question``);
* a ``computed`` declaration on a ``bool`` field — ``{op, collection, values}``,
  a fact derived from one collection rather than asked.

The CLI's job is transport and cheap structural checks. ``items`` and
``computed`` are carried to the engine exactly as authored, gated on the engine
advertising them (an engine that does not model them accepts the upload and
drops them), preserved by a pull, and refused locally only for shapes that are
wrong on their face. Anything semantic is the engine's to judge.
"""

from __future__ import annotations

import inspect
import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
import typer
import yaml
from typer.testing import CliRunner

from aethis_cli.client import AethisClient
from aethis_cli.commands import generate_cmd, rulebooks_cmd

BASE = "https://engine.example"

ITEMS = {
    "sort": "Enum",
    "enum_values": ["zero_g_operations", "eva_basic", "medical_officer"],
    "max_items": 50,
    "completion_question": "Any other certifications?",
}
COMPUTED = {
    "op": "any_in",
    "collection": "crew.certifications_held",
    "values": ["eva_basic", "medical_officer"],
}

COLLECTION_FIELDS = """\
fields:
  - key: crew.certifications_held
    type: collection
    question: Which certifications do you hold?
    items:
      sort: Enum
      enum_values: [zero_g_operations, eva_basic, medical_officer]
      max_items: 50
      completion_question: Any other certifications?
  - key: crew.holds_accepted_certification
    type: bool
    computed:
      op: any_in
      collection: crew.certifications_held
      values: [eva_basic, medical_officer]
  - key: crew.age
    type: int
"""

ENGINE_BASE = {"key", "sort", "enum_values", "value_space", "question", "label"}
ENGINE_WITH_COLLECTIONS = ENGINE_BASE | {"items", "computed"}


def _project(tmp_path, body: str):
    path = tmp_path / "fields" / "fields.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return tmp_path


def _client(properties=ENGINE_WITH_COLLECTIONS) -> MagicMock:
    client = MagicMock()
    client.expected_field_spec_properties.return_value = properties
    client.base_url = BASE
    return client


def _flat(capsys) -> str:
    return " ".join(capsys.readouterr().out.split())


def _openapi(properties) -> dict:
    return {"components": {"schemas": {"ExpectedFieldSpec": {"properties": {p: {} for p in properties}}}}}


def _collection(**over) -> dict:
    field = {"key": "crew.certifications_held", "type": "collection", "items": dict(ITEMS)}
    field.update(over)
    return field


def _computed(**over) -> dict:
    field = {"key": "crew.holds_accepted_certification", "type": "bool", "computed": dict(COMPUTED)}
    field.update(over)
    return field


# --- the type is accepted ---------------------------------------------------


def test_collection_is_an_accepted_field_type():
    assert "collection" in generate_cmd.VALID_FIELD_TYPES
    assert generate_cmd.validate_fields_list([_collection(), _computed()]) == []


def test_the_authored_example_validates_clean():
    assert generate_cmd.validate_fields_list(yaml.safe_load(COLLECTION_FIELDS)["fields"]) == []


def test_server_collection_maps_back_to_the_on_disk_short_form():
    assert generate_cmd._safe_field_type("Collection", None) == "collection"
    assert generate_cmd._normalise_field_type("COLLECTION") == "collection"


# --- collection validation --------------------------------------------------


def test_a_collection_without_items_is_rejected():
    errors = generate_cmd.validate_fields_list([{"key": "crew.certifications_held", "type": "collection"}])
    assert any("collection" in e and "items" in e for e in errors)


def test_a_collection_with_neither_item_members_nor_a_value_space_is_rejected():
    errors = generate_cmd.validate_fields_list([_collection(items={"sort": "Enum"})])
    assert any("enum_values" in e and "value_space" in e for e in errors)


def test_a_collection_with_empty_item_members_is_rejected():
    errors = generate_cmd.validate_fields_list([_collection(items={"sort": "Enum", "enum_values": []})])
    assert any("enum_values" in e for e in errors)


def test_a_collection_may_name_a_value_space_instead_of_inline_members():
    field = _collection(items={"sort": "Enum", "max_items": 10}, value_space="certifications")
    assert generate_cmd.validate_fields_list([field]) == []


def test_a_value_space_on_a_collection_is_not_refused_as_enum_only():
    errors = generate_cmd.validate_fields_list([_collection(value_space="certifications")])
    assert errors == []


def test_a_value_space_is_still_refused_on_a_scalar_field():
    errors = generate_cmd.validate_fields_list([{"key": "crew.age", "type": "int", "value_space": "certifications"}])
    assert any("value_space" in e and "int" in e for e in errors)


def test_items_that_are_not_a_mapping_are_rejected():
    errors = generate_cmd.validate_fields_list([_collection(items=["eva_basic"])])
    assert any("items" in e and "mapping" in e for e in errors)


def test_item_members_that_are_not_a_list_are_rejected():
    errors = generate_cmd.validate_fields_list([_collection(items={"sort": "Enum", "enum_values": "eva_basic"})])
    assert any("enum_values" in e and "list" in e for e in errors)


@pytest.mark.parametrize("ftype", ["enum", "int", "bool", "string"])
def test_items_on_a_non_collection_is_rejected(ftype):
    field = {"key": "crew.certification", "type": ftype, "items": dict(ITEMS)}
    if ftype == "enum":
        field["enum_values"] = ["eva_basic"]
    errors = generate_cmd.validate_fields_list([field])
    assert any("items" in e and ftype in e for e in errors)


@pytest.mark.parametrize(
    "field, named",
    [
        ({"key": "x", "type": "bool", "items": None}, "items"),
        ({"key": "x", "type": "bool", "computed": None}, "computed"),
        ({"key": "c", "type": "collection", "items": None}, "items"),
        ({"key": "x", "type": "bool", "computed": None, "items": {"sort": "Enum"}}, "computed"),
    ],
)
def test_an_explicit_null_declaration_is_an_error_not_an_absence(field, named):
    """Finding 8: the engine rejects an explicit null `items` or `computed`."""
    errors = generate_cmd.validate_fields_list([field, _collection(key="y")] if field["key"] == "x" else [field])
    assert any(named in e and "null" in e for e in errors)


@pytest.mark.parametrize("items", [{"enum_values": ["a", "b"]}, {"sort": "", "enum_values": ["a"]}, {"sort": 3}])
def test_items_must_carry_its_sort_as_text(items):
    """Finding 8: the engine requires `items.sort`."""
    errors = generate_cmd.validate_fields_list([_collection(items=items)])
    assert any("items.sort" in e for e in errors)


# --- computed validation ----------------------------------------------------


def test_computed_on_a_non_bool_field_is_rejected():
    field = {"key": "crew.score", "type": "int", "computed": dict(COMPUTED)}
    errors = generate_cmd.validate_fields_list([field, _collection()])
    assert any("computed" in e and "int" in e for e in errors)


def test_computed_that_is_not_a_mapping_is_rejected():
    errors = generate_cmd.validate_fields_list([_collection(), _computed(computed=["eva_basic"])])
    assert any("computed" in e and "mapping" in e for e in errors)


def test_computed_naming_a_field_that_is_not_a_collection_is_rejected():
    fields = [{"key": "crew.certifications_held", "type": "string"}, _computed()]
    errors = generate_cmd.validate_fields_list(fields)
    assert any("crew.certifications_held" in e and "not a collection" in e for e in errors)


def test_computed_naming_a_field_that_does_not_exist_is_rejected():
    errors = generate_cmd.validate_fields_list([_computed()])
    assert any("crew.certifications_held" in e and "not declared" in e for e in errors)


def test_validation_takes_one_layer_only():
    """A computed field reads a collection declared in the SAME file: the engine
    needs both in one pin set, so there is no cross-layer resolution to offer."""
    assert "external_fields" not in inspect.signature(generate_cmd.validate_fields_list).parameters
    assert not hasattr(generate_cmd, "enclosing_rulebook_fields")


def test_two_computed_fields_over_one_collection_are_rejected():
    second = _computed(key="crew.holds_other_certification")
    errors = generate_cmd.validate_fields_list([_collection(), _computed(), second])
    assert len([e for e in errors if "already read" in e]) == 1
    assert any("crew.holds_other_certification" in e and "crew.holds_accepted_certification" in e for e in errors)


@pytest.mark.parametrize("values", [[], None, "eva_basic"])
def test_computed_with_no_values_is_rejected(values):
    computed = {**COMPUTED, "values": values}
    errors = generate_cmd.validate_fields_list([_collection(), _computed(computed=computed)])
    assert any("computed" in e and "values" in e for e in errors)


@pytest.mark.parametrize("missing", ["op", "collection", "values"])
def test_computed_missing_a_required_part_is_rejected(missing):
    computed = {k: v for k, v in COMPUTED.items() if k != missing}
    errors = generate_cmd.validate_fields_list([_collection(), _computed(computed=computed)])
    assert any("computed" in e and missing in e for e in errors)


def test_computed_does_not_refuse_an_operator_it_has_not_heard_of():
    """The engine owns the operator set; a newer engine's operator must not be
    blocked by an older CLI."""
    computed = {**COMPUTED, "op": "all_in"}
    assert generate_cmd.validate_fields_list([_collection(), _computed(computed=computed)]) == []


# --- upload: the payload carries both declarations as authored ----------------


def test_upload_carries_items_and_computed_as_authored(tmp_path):
    client = _client()

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, COLLECTION_FIELDS))

    _, expected_fields = client.set_field_spec.call_args.args
    assert expected_fields == [
        {
            "key": "crew.certifications_held",
            "sort": "collection",
            "question": "Which certifications do you hold?",
            "items": ITEMS,
        },
        {"key": "crew.holds_accepted_certification", "sort": "bool", "computed": COMPUTED},
        {"key": "crew.age", "sort": "int"},
    ]
    # Byte-for-byte, not merely equal after normalisation: member order is
    # meaning, and key order is what a reviewer diffs.
    assert json.dumps(expected_fields[0]["items"]) == json.dumps(ITEMS)
    assert json.dumps(expected_fields[1]["computed"]) == json.dumps(COMPUTED)


def test_the_payload_is_a_copy_not_the_parsed_structure(tmp_path, monkeypatch):
    """Mutating what was sent must not reach back into the authored structure,
    and the nested member list must not be shared.

    The push re-parses the file on every call, so the parsed map is pinned by
    patching the parser: only then is the structure the payload was built from
    the one this test holds.
    """
    client = _client()
    project = _project(tmp_path, COLLECTION_FIELDS)
    parsed = generate_cmd._merged_field_map(project)
    monkeypatch.setattr(generate_cmd, "_merged_field_map", lambda _dir: parsed)

    generate_cmd._push_field_vocabulary(client, "proj_1", project)

    _, expected_fields = client.set_field_spec.call_args.args
    assert expected_fields[0]["items"]["enum_values"] is not parsed["crew.certifications_held"]["items"]["enum_values"]
    assert (
        expected_fields[1]["computed"]["values"]
        is not parsed["crew.holds_accepted_certification"]["computed"]["values"]
    )


def test_a_project_without_either_declaration_is_unchanged_and_never_probes(tmp_path):
    client = _client(ENGINE_BASE)
    project = _project(tmp_path, "fields:\n  - key: crew.age\n    type: int\n")

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    _, expected_fields = client.set_field_spec.call_args.args
    assert expected_fields == [{"key": "crew.age", "sort": "int"}]
    client.expected_field_spec_properties.assert_not_called()


@pytest.mark.parametrize("missing", ["items", "computed"])
def test_an_engine_that_does_not_model_a_declaration_is_refused_before_the_push(missing, tmp_path, capsys):
    client = _client(ENGINE_WITH_COLLECTIONS - {missing})

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, COLLECTION_FIELDS))

    client.set_field_spec.assert_not_called()
    client.add_guidance.assert_not_called()
    out = _flat(capsys)
    assert f"does not carry {missing}" in out


@respx.mock(base_url=BASE)
def test_the_real_client_posts_the_declarations_to_an_engine_that_models_them(respx_mock, tmp_path):
    respx_mock.get("/openapi.json").mock(return_value=httpx.Response(200, json=_openapi(ENGINE_WITH_COLLECTIONS)))
    spec = respx_mock.post("/api/v1/public/projects/proj_1/fields/spec").mock(return_value=httpx.Response(200, json={}))
    respx_mock.post("/api/v1/public/projects/proj_1/guidance").mock(return_value=httpx.Response(200, json={}))

    with AethisClient("ak", BASE) as client:
        generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, COLLECTION_FIELDS))

    sent = json.loads(spec.calls.last.request.content)["expected_fields"]
    assert sent[0]["sort"] == "collection"
    assert sent[0]["items"] == ITEMS
    assert sent[1]["computed"] == COMPUTED


@respx.mock(base_url=BASE, assert_all_called=False)
def test_the_real_client_refuses_an_engine_whose_schema_lacks_the_declarations(respx_mock, tmp_path, capsys):
    respx_mock.get("/openapi.json").mock(return_value=httpx.Response(200, json=_openapi(ENGINE_BASE)))
    spec = respx_mock.post("/api/v1/public/projects/proj_1/fields/spec").mock(return_value=httpx.Response(200, json={}))

    with AethisClient("ak", BASE) as client:
        with pytest.raises(typer.Exit):
            generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, COLLECTION_FIELDS))

    assert spec.call_count == 0
    assert "does not carry computed, items" in _flat(capsys)


# --- the read-back path: a round trip to disk --------------------------------


def test_write_back_keeps_both_declarations_in_their_modelled_place():
    """The fixture carries a key that sorts after them, so a modelled position is
    told apart from being swept up by the trailing keep-anything-unknown loop."""
    collection = generate_cmd._field_to_yaml_dict({**_collection(), "hints": ["Ask which they hold."]})
    computed = generate_cmd._field_to_yaml_dict({**_computed(), "hints": ["Derived."]})

    assert collection["items"] == ITEMS
    assert list(collection) == ["key", "type", "items", "hints"]
    assert computed["computed"] == COMPUTED
    assert list(computed) == ["key", "type", "computed", "hints"]


def test_write_back_keeps_a_declared_empty_items_by_presence():
    """An explicit empty declaration is the author's, not an absence to sweep."""
    assert generate_cmd._field_to_yaml_dict({"key": "x", "type": "collection", "items": {}})["items"] == {}


def test_a_fields_yaml_round_trips_through_disk_intact(tmp_path):
    path = tmp_path / "fields" / "fields.yaml"
    path.parent.mkdir()
    path.write_text(COLLECTION_FIELDS)

    generate_cmd._write_fields_yaml(path, generate_cmd._parse_fields_yaml(path))

    reread = generate_cmd._parse_fields_yaml(path)
    assert reread["crew.certifications_held"]["items"] == ITEMS
    assert reread["crew.certifications_held"]["type"] == "collection"
    assert reread["crew.holds_accepted_certification"]["computed"] == COMPUTED
    assert generate_cmd.validate_fields_list(list(reread.values())) == []


def _pull_cfg(tmp_path):
    return SimpleNamespace(
        config_path=tmp_path,
        project_id="proj_1",
        base_url="https://api.aethis.ai",
        project="p",
        anthropic_key_env="ANTHROPIC_API_KEY",
    )


# The engine's /schema publishes a collection with its ``items`` object, and a
# computed field only as ``computed_from: <collection key>`` -- the full
# {op, collection, values} declaration is NOT published, so a pull cannot
# reconstruct it. This is the real response shape, not a fixture that omits it.
SCHEMA_ITEMS = {"sort": "Enum", "enum_values": ["a", "b"]}
REAL_SCHEMA = {
    "fields": [
        {"field_id": "c", "field_type": "Collection", "items": SCHEMA_ITEMS},
        {"field_id": "derived", "field_type": "boolean", "computed_from": "c"},
    ]
}


def _pull(tmp_path, monkeypatch, schema):
    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.get_schema.return_value = schema
    with (
        patch("aethis_cli.commands.fields_cmd.load_project_config", return_value=_pull_cfg(tmp_path)),
        patch("aethis_cli.commands.fields_cmd.resolve_api_key", return_value="ak"),
        patch("aethis_cli.commands.fields_cmd.make_authed_client", return_value=client),
    ):
        from aethis_cli.main import app

        result = CliRunner().invoke(app, ["fields", "pull", "-b", "rs_1"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return generate_cmd._parse_fields_yaml(tmp_path / "fields" / "fields.yaml"), " ".join(result.output.split())


def test_a_clean_pull_writes_the_collection_and_its_items(tmp_path, monkeypatch):
    parsed, _ = _pull(tmp_path, monkeypatch, REAL_SCHEMA)

    assert parsed["c"]["type"] == "collection"
    assert parsed["c"]["items"] == SCHEMA_ITEMS
    assert generate_cmd.validate_fields_list([parsed["c"]]) == []


@pytest.mark.parametrize("spelling", ["Collection", "collection", "COLLECTION"])
def test_a_pulled_collection_type_is_matched_in_any_case(spelling, tmp_path, monkeypatch):
    schema = {"fields": [{"field_id": "c", "field_type": spelling, "items": SCHEMA_ITEMS}]}
    parsed, _ = _pull(tmp_path, monkeypatch, schema)
    assert parsed["c"]["type"] == "collection"
    assert parsed["c"]["items"] == SCHEMA_ITEMS


def test_a_clean_pull_never_guesses_a_computed_declaration_and_says_so(tmp_path, monkeypatch):
    parsed, out = _pull(tmp_path, monkeypatch, REAL_SCHEMA)

    assert parsed["derived"]["type"] == "bool"
    assert "computed" not in parsed["derived"]
    assert "derived" in out
    assert "computed" in out and "authored locally" in out


def test_a_pull_keeps_a_local_computed_declaration_and_does_not_warn(tmp_path, monkeypatch):
    (tmp_path / "fields").mkdir()
    local = {"fields": [{"key": "derived", "type": "bool", "computed": dict(COMPUTED, collection="c")}]}
    (tmp_path / "fields" / "fields.yaml").write_text(yaml.safe_dump(local))

    parsed, out = _pull(tmp_path, monkeypatch, REAL_SCHEMA)

    assert parsed["derived"]["computed"] == dict(COMPUTED, collection="c")
    assert "authored locally" not in out


def test_a_pull_keeps_local_items_when_the_server_row_carries_none(tmp_path, monkeypatch):
    (tmp_path / "fields").mkdir()
    (tmp_path / "fields" / "fields.yaml").write_text(COLLECTION_FIELDS)
    schema = {
        "fields": [
            {"field_id": "crew.certifications_held", "field_type": "Collection"},
            {"field_id": "crew.holds_accepted_certification", "field_type": "boolean", "computed_from": "x"},
            {"field_id": "crew.age", "field_type": "integer"},
        ]
    }

    parsed, _ = _pull(tmp_path, monkeypatch, schema)

    assert parsed["crew.certifications_held"]["items"] == ITEMS
    assert parsed["crew.holds_accepted_certification"]["computed"] == COMPUTED


def test_a_pull_drops_items_from_a_field_the_server_no_longer_calls_a_collection(tmp_path, monkeypatch):
    (tmp_path / "fields").mkdir()
    (tmp_path / "fields" / "fields.yaml").write_text(yaml.safe_dump({"fields": [_collection(key="c")]}))

    parsed, _ = _pull(tmp_path, monkeypatch, {"fields": [{"field_id": "c", "field_type": "string"}]})

    assert parsed["c"]["type"] == "string"
    assert "items" not in parsed["c"]


def test_fields_validate_reports_a_collection_without_items(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fields").mkdir()
    (tmp_path / "fields" / "fields.yaml").write_text(
        "fields:\n  - key: crew.certifications_held\n    type: collection\n"
    )
    from aethis_cli.main import app

    with patch("aethis_cli.commands.fields_cmd.load_project_config", return_value=_pull_cfg(tmp_path)):
        result = CliRunner().invoke(app, ["fields", "validate"], catch_exceptions=False)

    assert result.exit_code == 1
    assert "items" in result.output


# --- one layer only: a rulebook never speaks for a collection or computed key --
#
# A collection and the computed field reading it are declared in the ruleset's
# OWN fields.yaml, both in the one pin set the engine requires. A rulebook row
# for such a key may carry only its identity -- key and one of sort/type -- and
# never replaces the member's declaration.

# The engine advertises ``items`` but not ``computed``.
ENGINE_WITH_ITEMS_ONLY = ENGINE_BASE | {"items"}

MEMBER_FIELDS = [
    {"key": "c", "type": "collection", "items": {"sort": "Enum", "enum_values": ["a", "b"]}},
    {"key": "derived", "type": "bool", "computed": {"op": "any_in", "collection": "c", "values": ["a"]}},
]


def _layers(tmp_path, rulebook_rows, member_rows=None):
    rulebook = tmp_path / "rb"
    member = rulebook / "rulesets" / "crew"
    (rulebook / "fields").mkdir(parents=True)
    (member / "fields").mkdir(parents=True)
    (rulebook / "aethis.yaml").write_text("project: rb\nkind: rulebook\n")
    (member / "aethis.yaml").write_text("project: crew\n")
    (rulebook / "fields" / "fields.yaml").write_text(yaml.safe_dump({"fields": rulebook_rows}))
    (member / "fields" / "fields.yaml").write_text(yaml.safe_dump({"fields": member_rows or MEMBER_FIELDS}))
    return member


def _pushed_by_key(client) -> dict:
    _, expected_fields = client.set_field_spec.call_args.args
    return {f["key"]: f for f in expected_fields}


def test_a_rulebook_row_cannot_erase_the_computed_the_member_declares(tmp_path, capsys):
    """Finding 1: the rulebook's `{key: derived, sort: Bool}` used to replace the
    member's row, so the posted pin lost `computed` -- and an engine that does not
    advertise `computed` passed the capability check because nothing was left to
    check. The member's declaration is what is pinned, and what is gated."""
    member = _layers(tmp_path, [{"key": "derived", "sort": "Bool"}])
    client = _client(ENGINE_WITH_ITEMS_ONLY)

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", member)

    client.set_field_spec.assert_not_called()
    assert "does not carry computed" in _flat(capsys)


def test_the_members_computed_is_what_is_pinned_when_the_engine_supports_it(tmp_path):
    member = _layers(tmp_path, [{"key": "derived", "sort": "Bool"}])
    client = _client()

    generate_cmd._upload_field_vocabulary(client, "proj_1", member)

    assert _pushed_by_key(client)["derived"]["computed"] == MEMBER_FIELDS[1]["computed"]


def test_an_identity_only_rulebook_row_for_a_collection_is_accepted_and_keeps_items(tmp_path):
    """The row the set-fields guard permits must also work as a rulebook layer:
    it validates, and it does not erase the member's `items`."""
    member = _layers(tmp_path, [{"key": "c", "sort": "Collection"}])
    client = _client()

    generate_cmd._upload_field_vocabulary(client, "proj_1", member)

    assert _pushed_by_key(client)["c"]["items"] == MEMBER_FIELDS[0]["items"]


@pytest.mark.parametrize(
    "row, named",
    [
        ({"key": "derived", "type": "bool", "question": "Choose an answer"}, "question"),
        ({"key": "c", "type": "collection", "question": "Which?"}, "question"),
        ({"key": "c", "type": "collection", "enum_values": ["a"]}, "enum_values"),
        ({"key": "c", "sort": "Collection", "type": "collection"}, "sort and type"),
    ],
)
def test_a_rulebook_row_that_says_more_than_identity_about_a_protected_key_is_refused(row, named, tmp_path, capsys):
    member = _layers(tmp_path, [row])
    client = _client()

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", member)

    client.set_field_spec.assert_not_called()
    out = _flat(capsys)
    assert row["key"] in out and named in out and "only" in out


@pytest.mark.parametrize("declaration", ["items", "computed"])
def test_a_rulebook_file_that_declares_items_or_computed_is_refused(declaration, tmp_path, capsys):
    """Finding 3/6: a rulebook declaring a collection or computed field would
    otherwise be silently dropped from the member's pin set -- or counted as a
    second reader the member's own file never shows."""
    identity = {"key": "c", "sort": "Collection"}
    row = (
        _collection(key="z") if declaration == "items" else _computed(key="z", computed=dict(COMPUTED, collection="c"))
    )
    # The computed case carries the collection identity too, so the only fault
    # in the rulebook file is the declaration under test.
    member = _layers(tmp_path, [row] if declaration == "items" else [identity, row])
    client = _client()

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", member)

    client.set_field_spec.assert_not_called()
    out = _flat(capsys)
    assert "'z'" in out and f"declares {declaration}" in out and "may not declare items or computed" in out


def test_a_computed_reading_a_collection_only_the_rulebook_declares_is_refused(tmp_path, capsys):
    """Finding 3: the engine wants the collection in the same pin set, and the
    pushed payload would hold only `derived`."""
    member = _layers(tmp_path, [{"key": "c", "sort": "Collection"}], member_rows=[MEMBER_FIELDS[1]])
    client = _client()

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", member)

    client.set_field_spec.assert_not_called()
    assert "not declared" in _flat(capsys)


def test_a_second_computed_reader_hiding_in_the_rulebook_is_refused(tmp_path, capsys):
    """Finding 6: the member reads `c` from `a`; the rulebook's `z` carries a
    second computed over the same collection while the member's own `z` is a
    plain stub, so per-file duplicate checks both passed."""
    rulebook_rows = [
        _collection(key="c", items={"sort": "Enum", "enum_values": ["a", "b"]}),
        {"key": "z", "type": "bool", "computed": {"op": "any_in", "collection": "c", "values": ["a"]}},
    ]
    member_rows = [
        MEMBER_FIELDS[0],
        {"key": "a", "type": "bool", "computed": {"op": "any_in", "collection": "c", "values": ["a"]}},
        {"key": "z", "type": "bool"},
    ]
    member = _layers(tmp_path, rulebook_rows, member_rows)
    client = _client()

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", member)

    client.set_field_spec.assert_not_called()
    out = _flat(capsys)
    assert "'z'" in out and "may not declare items or computed" in out


def test_an_unrelated_shared_key_still_lets_the_rulebook_win(tmp_path):
    """The override stays for every key the member does not declare as a
    collection or computed."""
    member = _layers(
        tmp_path,
        [{"key": "crew.age", "type": "int", "question": "Rulebook wording?"}],
        member_rows=[*MEMBER_FIELDS, {"key": "crew.age", "type": "int", "question": "Member wording?"}],
    )
    client = _client()

    generate_cmd._upload_field_vocabulary(client, "proj_1", member)

    assert _pushed_by_key(client)["crew.age"]["question"] == "Rulebook wording?"


def test_fields_validate_refuses_a_rulebook_row_that_overreaches(tmp_path, monkeypatch):
    member = _layers(tmp_path, [{"key": "derived", "type": "bool", "question": "x"}])
    monkeypatch.chdir(member)
    from aethis_cli.main import app

    with patch("aethis_cli.commands.fields_cmd.load_project_config", return_value=_pull_cfg(member)):
        result = CliRunner().invoke(app, ["fields", "validate"], catch_exceptions=False)

    assert result.exit_code == 1
    assert "derived" in result.output


# --- the capability gate fails closed, and runs before anything is written ----


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(httpx.Response(200, json={"components": {"schemas": {}}}), id="schema-without-the-model"),
        pytest.param(httpx.Response(404), id="404"),
        pytest.param(httpx.Response(500), id="500"),
        pytest.param(httpx.ConnectTimeout("timed out"), id="timeout"),
    ],
)
@respx.mock(base_url=BASE, assert_all_called=False)
def test_an_unreadable_engine_schema_refuses_a_collection_project(answer, respx_mock, tmp_path, capsys):
    """Finding 2: an unreadable advertisement used to print "Proceeding" and
    send both declarations to an engine that might drop them silently."""
    respx_mock.get("/openapi.json").mock(side_effect=answer) if isinstance(answer, Exception) else respx_mock.get(
        "/openapi.json"
    ).mock(return_value=answer)
    spec = respx_mock.post("/api/v1/public/projects/proj_1/fields/spec").mock(return_value=httpx.Response(200, json={}))

    with AethisClient("ak", BASE) as client:
        with pytest.raises(typer.Exit):
            generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, COLLECTION_FIELDS))

    assert spec.call_count == 0
    assert "could not read" in _flat(capsys).lower()


def test_an_unreadable_schema_still_lets_a_project_declaring_neither_proceed(tmp_path):
    client = _client(None)

    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path, "fields:\n  - key: a\n    type: int\n"))

    client.set_field_spec.assert_called_once()


def test_the_older_gated_keys_keep_failing_open_on_an_unreadable_schema(tmp_path, capsys):
    """Out of scope for this change: only items and computed fail closed."""
    client = _client(None)
    project = _project(tmp_path, "fields:\n  - key: a\n    type: enum\n    enum_values: [x]\n    enum_labels: {x: X}\n")

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    client.set_field_spec.assert_called_once()
    assert "Proceeding" in _flat(capsys)


MUTATING_CALLS = (
    "create_project",
    "upload_sources",
    "update_source",
    "add_guidance",
    "put_value_space",
    "set_field_spec",
    "add_tests",
    "generate",
)


def _generate_client(properties) -> MagicMock:
    client = MagicMock()
    client.expected_field_spec_properties.return_value = properties
    client.list_sources.return_value = {"sources": []}
    client.upload_sources.return_value = {"new": 1, "reused": 0, "sources": [{"source_id": "s", "filename": "a.md"}]}
    client.generate.return_value = {"job_id": "job_1"}
    client.create_project.return_value = {"project_id": "proj_new"}
    client.last_rate_limit = None
    client.base_url = BASE
    return client


def _generate_project(tmp_path):
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "a.md").write_text("the source document")
    (tmp_path / "guidance").mkdir()
    (tmp_path / "guidance" / "hints.yaml").write_text("hints:\n  - Ask politely.\n")
    _project(tmp_path, COLLECTION_FIELDS)
    return tmp_path


@pytest.mark.parametrize("existing_project", [True, False], ids=["existing-project", "no-project-yet"])
def test_a_refused_engine_is_refused_before_anything_is_created_or_uploaded(existing_project, tmp_path, monkeypatch):
    """Finding 5: the rejection used to come after project creation, the source
    upload and the guidance upload, so a retry accumulated guidance."""
    project = _generate_project(tmp_path)
    client = _generate_client(ENGINE_BASE)  # models neither items nor computed
    cfg = SimpleNamespace(
        config_path=project, base_url=BASE, project_id="proj_abc" if existing_project else None, project="p"
    )
    monkeypatch.setattr(generate_cmd, "load_project_config", lambda: cfg)
    monkeypatch.setattr(generate_cmd, "resolve_api_key", lambda _cfg: "ak")
    monkeypatch.setattr(generate_cmd, "resolve_anthropic_key", lambda _cfg: None)
    monkeypatch.setattr(generate_cmd, "make_authed_client", lambda *_a, **_k: client)

    with pytest.raises(typer.Exit):
        generate_cmd._run_generate(project_id=None, poll=False, timeout=5)

    written = [name for name in MUTATING_CALLS if getattr(client, name).called]
    assert written == []
    client.get_project.assert_not_called()


def test_the_same_run_proceeds_against_an_engine_that_models_them(tmp_path, monkeypatch):
    """Control: the refusal above is the gate, not an unrelated failure."""
    project = _generate_project(tmp_path)
    client = _generate_client(ENGINE_WITH_COLLECTIONS)
    cfg = SimpleNamespace(config_path=project, base_url=BASE, project_id="proj_abc", project="p")
    monkeypatch.setattr(generate_cmd, "load_project_config", lambda: cfg)
    monkeypatch.setattr(generate_cmd, "resolve_api_key", lambda _cfg: "ak")
    monkeypatch.setattr(generate_cmd, "resolve_anthropic_key", lambda _cfg: None)
    monkeypatch.setattr(generate_cmd, "make_authed_client", lambda *_a, **_k: client)

    generate_cmd._run_generate(project_id=None, poll=False, timeout=5)

    client.upload_sources.assert_called()
    _, expected_fields = client.set_field_spec.call_args.args
    assert expected_fields[0]["items"] == ITEMS


# --- rulebooks set-fields: the vocabulary row carries only key and sort ------


def _rulebook_client() -> MagicMock:
    client = MagicMock()
    client.rulebook_field_spec_properties.return_value = {"key", "sort", "enum_values"}
    client.set_rulebook_fields.return_value = {"fields": [], "field_lock_state": "unlocked"}
    client.base_url = BASE
    return client


def _set_fields(tmp_path, monkeypatch, rows) -> MagicMock:
    client = _rulebook_client()
    monkeypatch.setattr(rulebooks_cmd, "load_client_or_fallback", lambda: (None, client))
    path = tmp_path / "fields.yaml"
    path.write_text(yaml.safe_dump({"fields": rows}))
    rulebooks_cmd.set_fields("rb_1", path)
    return client


def test_set_fields_posts_a_collection_row_that_carries_only_key_and_sort(tmp_path, monkeypatch):
    rows = [{"key": "crew.certifications_held", "sort": "Collection"}, {"key": "crew.age", "sort": "Int"}]

    client = _set_fields(tmp_path, monkeypatch, rows)

    client.set_rulebook_fields.assert_called_once_with("rb_1", rows)


@pytest.mark.parametrize(
    "row, named",
    [
        ({"key": "crew.certifications_held", "sort": "Collection", "items": dict(ITEMS)}, "items"),
        ({"key": "crew.holds_accepted_certification", "sort": "Bool", "computed": dict(COMPUTED)}, "computed"),
        ({"key": "crew.certifications_held", "sort": "Collection", "enum_values": ["eva_basic"]}, "enum_values"),
    ],
)
def test_set_fields_refuses_a_row_that_says_more_about_a_collection_or_computed_key(
    row, named, tmp_path, monkeypatch, capsys
):
    client = _rulebook_client()
    monkeypatch.setattr(rulebooks_cmd, "load_client_or_fallback", lambda: (None, client))
    path = tmp_path / "fields.yaml"
    path.write_text(yaml.safe_dump({"fields": [row]}))

    with pytest.raises(typer.Exit):
        rulebooks_cmd.set_fields("rb_1", path)

    client.set_rulebook_fields.assert_not_called()
    out = _flat(capsys)
    assert row["key"] in out
    assert named in out
    assert "only key and one of sort or type" in out


def test_set_fields_refuses_a_collection_row_that_carries_both_sort_and_type(tmp_path, monkeypatch, capsys):
    """Finding 7: `type` was whitelisted beside `sort`, so a row could say
    `sort: Collection` and `type: Bool` at once and pass."""
    client = _rulebook_client()
    monkeypatch.setattr(rulebooks_cmd, "load_client_or_fallback", lambda: (None, client))
    path = tmp_path / "fields.yaml"
    path.write_text(yaml.safe_dump({"fields": [{"key": "c", "sort": "Collection", "type": "Bool"}]}))

    with pytest.raises(typer.Exit):
        rulebooks_cmd.set_fields("rb_1", path)

    client.set_rulebook_fields.assert_not_called()
    assert "sort and type" in _flat(capsys)


def test_set_fields_accepts_a_collection_row_that_uses_type_alone(tmp_path, monkeypatch):
    rows = [{"key": "c", "type": "collection"}]

    client = _set_fields(tmp_path, monkeypatch, rows)

    client.set_rulebook_fields.assert_called_once_with("rb_1", rows)


def test_set_fields_leaves_a_plain_enum_row_with_members_alone(tmp_path, monkeypatch):
    rows = [{"key": "crew.rank", "sort": "Enum", "enum_values": ["pilot", "medic"]}]

    client = _set_fields(tmp_path, monkeypatch, rows)

    client.set_rulebook_fields.assert_called_once_with("rb_1", rows)


# --- the post-generation field diff reads a collection's members from items ----
#
# The engine's /schema publishes a collection with field_type "collection" and
# its members under ``items.enum_values`` -- never at the top level -- and a
# computed field only as ``computed_from``. A diff that read only the top-level
# ``enum_values`` reported every member of every collection as dropped.


def _schema_collection(members, field_id="c", field_type="collection"):
    return {
        "field_id": field_id,
        "field_type": field_type,
        "items": {"sort": "Enum", "enum_values": list(members), "max_items": 500, "completion_question": "More?"},
    }


def _diff(tmp_path, capsys, fields_yaml, schema_fields, **kwargs):
    path = tmp_path / "fields" / "fields.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fields_yaml)
    client = MagicMock()
    client.get_schema.return_value = {"fields": schema_fields}
    generate_cmd._report_field_diff(client, "rs_1", tmp_path, **kwargs)
    return client, " ".join(re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out).split())


INLINE_COLLECTION_PIN = (
    "fields:\n  - key: c\n    type: collection\n    items: {sort: Enum, enum_values: [a, b]}\n"
    "  - key: derived\n    type: bool\n    computed: {op: any_in, collection: c, values: [a]}\n"
)
DERIVED_SCHEMA = {"field_id": "derived", "field_type": "bool", "computed_from": "c"}


def test_an_inline_collection_whose_members_match_the_pin_is_clean(tmp_path, capsys):
    _, out = _diff(tmp_path, capsys, INLINE_COLLECTION_PIN, [_schema_collection(["b", "a"]), DERIVED_SCHEMA])

    assert "all 2 pinned field(s) were produced" in out
    assert "differ" not in out


@pytest.mark.parametrize("spelling", ["collection", "Collection", "COLLECTION"])
def test_the_collection_field_type_is_matched_in_any_case(spelling, tmp_path, capsys):
    _, out = _diff(
        tmp_path, capsys, INLINE_COLLECTION_PIN, [_schema_collection(["a", "b"], field_type=spelling), DERIVED_SCHEMA]
    )
    assert "differ" not in out


def test_an_inline_collection_that_grew_a_member_still_warns(tmp_path, capsys):
    _, out = _diff(tmp_path, capsys, INLINE_COLLECTION_PIN, [_schema_collection(["a", "b", "x"]), DERIVED_SCHEMA])

    assert "Enum members differ from the pin: c" in out
    assert "added x" in out
    assert "all 2 pinned field(s) were produced" not in out


def test_an_inline_collection_that_lost_a_member_still_warns(tmp_path, capsys):
    _, out = _diff(tmp_path, capsys, INLINE_COLLECTION_PIN, [_schema_collection(["a"]), DERIVED_SCHEMA])

    assert "Enum members differ from the pin: c" in out
    assert "dropped b" in out


def test_an_inline_collection_produced_with_no_items_at_all_warns(tmp_path, capsys):
    _, out = _diff(
        tmp_path,
        capsys,
        INLINE_COLLECTION_PIN,
        [{"field_id": "c", "field_type": "collection"}, DERIVED_SCHEMA],
    )
    assert "Enum members differ from the pin: c" in out
    assert "no members at all" in out


VALUE_SPACE_COLLECTION_PIN = (
    "fields:\n  - key: c\n    type: collection\n    value_space: certs\n    items: {sort: Enum}\n"
    "  - key: derived\n    type: bool\n    computed: {op: any_in, collection: c, values: [a]}\n"
)
RESOLVED = {"c": {"space": "certs", "version": 3, "space_id": "vs_1"}}


def _space_client(members):
    return {"members": list(members)}


def test_a_value_space_collection_whose_items_equal_the_space_is_verified_not_flagged(tmp_path, capsys):
    path = tmp_path / "fields" / "fields.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(VALUE_SPACE_COLLECTION_PIN)
    client = MagicMock()
    client.get_schema.return_value = {"fields": [_schema_collection(["a", "b", "c"]), DERIVED_SCHEMA]}
    client.get_value_space.return_value = _space_client(["c", "b", "a"])

    generate_cmd._report_field_diff(client, "rs_1", tmp_path, value_spaces_resolved=RESOLVED)
    out = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out).split())

    assert "c ← certs@v3 (3 members verified)" in out
    assert "differ" not in out
    assert "all 2 pinned field(s) were produced" in out


def test_a_value_space_collection_that_genuinely_differs_still_warns(tmp_path, capsys):
    path = tmp_path / "fields" / "fields.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(VALUE_SPACE_COLLECTION_PIN)
    client = MagicMock()
    client.get_schema.return_value = {"fields": [_schema_collection(["a", "b"]), DERIVED_SCHEMA]}
    client.get_value_space.return_value = _space_client(["a", "b", "c"])

    generate_cmd._report_field_diff(client, "rs_1", tmp_path, value_spaces_resolved=RESOLVED)
    out = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out).split())

    assert "Enum members differ from the pin: c" in out
    assert "dropped c" in out
