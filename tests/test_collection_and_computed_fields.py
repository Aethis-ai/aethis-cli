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

import json
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


def test_computed_resolves_its_collection_in_the_enclosing_rulebook_when_given():
    """A member ruleset's computed field may read a collection the rulebook
    declares; resolving only against the member's own file would false-positive."""
    errors = generate_cmd.validate_fields_list([_computed()], external_fields=[_collection()])
    assert errors == []


def test_a_collection_the_rulebook_declares_wrongly_is_still_caught_through_the_member():
    errors = generate_cmd.validate_fields_list(
        [_computed()], external_fields=[{"key": "crew.certifications_held", "type": "int"}]
    )
    assert any("not a collection" in e for e in errors)


def test_two_computed_fields_over_one_collection_are_rejected():
    second = _computed(key="crew.holds_other_certification")
    errors = generate_cmd.validate_fields_list([_collection(), _computed(), second])
    assert len([e for e in errors if "already read" in e]) == 1
    assert any("crew.holds_other_certification" in e and "crew.holds_accepted_certification" in e for e in errors)


def test_the_same_computed_key_in_member_and_rulebook_is_one_reader_not_two():
    """The rulebook's definition overrides a member's on a shared key, so the
    pair is one field."""
    errors = generate_cmd.validate_fields_list([_collection(), _computed()], external_fields=[_computed()])
    assert not any("already read" in e for e in errors)


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


def test_fields_pull_leaves_authored_declarations_intact(tmp_path, monkeypatch):
    """The server schema is authoritative for key and type; the authored
    ``items`` / ``computed`` are local truth and survive the merge."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fields").mkdir()
    (tmp_path / "fields" / "fields.yaml").write_text(COLLECTION_FIELDS)
    client = MagicMock()
    client.get_schema.return_value = {
        "fields": [
            {"field_id": "crew.certifications_held", "field_type": "Collection"},
            {"field_id": "crew.holds_accepted_certification", "field_type": "boolean"},
            {"field_id": "crew.age", "field_type": "integer"},
        ]
    }

    with (
        patch("aethis_cli.commands.fields_cmd.load_project_config", return_value=_pull_cfg(tmp_path)),
        patch("aethis_cli.commands.fields_cmd.resolve_api_key", return_value="ak"),
        patch("aethis_cli.commands.fields_cmd.make_authed_client", return_value=client),
    ):
        from aethis_cli.main import app

        result = CliRunner().invoke(app, ["fields", "pull", "-b", "rs_1"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    parsed = generate_cmd._parse_fields_yaml(tmp_path / "fields" / "fields.yaml")
    assert parsed["crew.certifications_held"]["type"] == "collection"
    assert parsed["crew.certifications_held"]["items"] == ITEMS
    assert parsed["crew.holds_accepted_certification"]["computed"] == COMPUTED


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


def test_fields_validate_resolves_a_collection_declared_in_the_enclosing_rulebook(tmp_path, monkeypatch):
    rulebook = tmp_path / "rb"
    member = rulebook / "rulesets" / "crew"
    (rulebook / "fields").mkdir(parents=True)
    (member / "fields").mkdir(parents=True)
    (rulebook / "aethis.yaml").write_text("project: rb\nkind: rulebook\n")
    (member / "aethis.yaml").write_text("project: crew\n")
    (rulebook / "fields" / "fields.yaml").write_text(yaml.safe_dump({"fields": [_collection()]}))
    (member / "fields" / "fields.yaml").write_text(yaml.safe_dump({"fields": [_computed()]}))
    monkeypatch.chdir(member)
    from aethis_cli.main import app

    with patch("aethis_cli.commands.fields_cmd.load_project_config", return_value=_pull_cfg(member)):
        result = CliRunner().invoke(app, ["fields", "validate"], catch_exceptions=False)

    assert result.exit_code == 0, result.output


def test_upload_validates_a_member_against_the_rulebook_it_reads_from(tmp_path):
    rulebook = tmp_path / "rb"
    member = rulebook / "rulesets" / "crew"
    (rulebook / "fields").mkdir(parents=True)
    (member / "fields").mkdir(parents=True)
    (rulebook / "aethis.yaml").write_text("project: rb\nkind: rulebook\n")
    (member / "aethis.yaml").write_text("project: crew\n")
    (rulebook / "fields" / "fields.yaml").write_text(yaml.safe_dump({"fields": [_collection()]}))
    (member / "fields" / "fields.yaml").write_text(yaml.safe_dump({"fields": [_computed()]}))

    generate_cmd._validate_project_fields(member)  # must not exit


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
    assert "only key and sort" in out


def test_set_fields_leaves_a_plain_enum_row_with_members_alone(tmp_path, monkeypatch):
    rows = [{"key": "crew.rank", "sort": "Enum", "enum_values": ["pilot", "medic"]}]

    client = _set_fields(tmp_path, monkeypatch, rows)

    client.set_rulebook_fields.assert_called_once_with("rb_1", rows)
