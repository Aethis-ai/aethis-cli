"""Collection fields, computed declarations and question prerequisites reach the engine as authored.

The engine owns the shape of all three (it advertises them on the field-spec
schema and validates them strictly). The CLI's job is to send them unchanged
when the engine says it keeps them, and to refuse loudly when it does not.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import typer

from aethis_cli.commands import generate_cmd


BASE = {"key", "sort", "enum_values", "value_space", "enum_labels", "label", "question"}
NEW = {"items", "computed", "question_prerequisite"}

FIELDS = """\
fields:
  - key: owner.vehicles
    type: collection
    value_space: acme/vehicle-makes
    enum_labels: {toyota: Toyota, ford: Ford}
    items: {sort: Enum, min_items: 1, completion_question: "Is that every vehicle you own?"}
  - key: owner.has_vehicles
    type: bool
  - key: owner.owns_listed_make
    type: bool
    question_prerequisite: {field: owner.has_vehicles, equals: true}
    computed: {op: any_in, collection: owner.vehicles, values: [toyota]}
  - key: owner.has_licence_a
    type: bool
  - key: owner.has_licence_b
    type: bool
  - key: owner.has_any_licence
    type: bool
    computed: {op: any_true, fields: [owner.has_licence_a, owner.has_licence_b]}
"""


def _project(tmp_path, body: str = FIELDS):
    fields = tmp_path / "fields" / "fields.yaml"
    fields.parent.mkdir(parents=True)
    fields.write_text(body)
    return tmp_path


def _client(properties):
    client = MagicMock()
    client.expected_field_spec_properties.return_value = properties
    client.base_url = "https://engine.example"
    return client


def test_fields_file_with_all_three_is_valid(tmp_path):
    import yaml

    rows = yaml.safe_load(FIELDS)["fields"]
    assert generate_cmd.validate_fields_list(rows) == []


def test_all_three_transported_exactly_as_authored(tmp_path, monkeypatch):
    monkeypatch.setattr(generate_cmd, "_sync_value_spaces", lambda *a, **k: None)
    client = _client(BASE | NEW)
    generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path))
    sent = {f["key"]: f for f in client.set_field_spec.call_args.args[1]}

    assert sent["owner.vehicles"]["sort"] == "collection"
    assert sent["owner.vehicles"]["value_space"] == "acme/vehicle-makes"
    assert sent["owner.vehicles"]["enum_labels"] == {"toyota": "Toyota", "ford": "Ford"}
    assert sent["owner.vehicles"]["items"] == {
        "sort": "Enum",
        "min_items": 1,
        "completion_question": "Is that every vehicle you own?",
    }
    assert sent["owner.owns_listed_make"]["computed"] == {
        "op": "any_in",
        "collection": "owner.vehicles",
        "values": ["toyota"],
    }
    assert sent["owner.owns_listed_make"]["question_prerequisite"] == {
        "field": "owner.has_vehicles",
        "equals": True,
    }
    assert sent["owner.has_any_licence"]["computed"] == {
        "op": "any_true",
        "fields": ["owner.has_licence_a", "owner.has_licence_b"],
    }
    assert "computed" not in sent["owner.has_vehicles"]


@pytest.mark.parametrize("missing", sorted(NEW))
def test_engine_without_the_property_refuses_before_mutation(tmp_path, monkeypatch, missing):
    monkeypatch.setattr(generate_cmd, "_sync_value_spaces", lambda *a, **k: None)
    client = _client((BASE | NEW) - {missing})
    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", _project(tmp_path))
    client.set_field_spec.assert_not_called()


@pytest.mark.parametrize(
    "row, expected",
    [
        ("  - {key: a.list, type: collection, value_space: acme/x}\n", "declares no items"),
        ("  - {key: a.flag, type: bool, computed: [any_true]}\n", "computed"),
        ("  - {key: a.flag, type: bool, question_prerequisite: a.other}\n", "question_prerequisite"),
        ("  - {key: a.flag, type: bool, items: {sort: Enum}}\n", "items"),
    ],
)
def test_malformed_declarations_are_reported_locally(row, expected):
    import yaml

    rows = yaml.safe_load("fields:\n" + row)["fields"]
    errors = generate_cmd.validate_fields_list(rows)
    assert any(expected in e for e in errors), errors


def test_value_space_collection_members_are_read_from_items_after_generation(tmp_path, capsys):
    """A collection keeps its members under ``items``; reading only the top level reported false drift."""
    project = _project(
        tmp_path,
        "fields:\n  - {key: owner.vehicles, type: collection, value_space: acme/vehicle-makes, items: {sort: Enum}}\n",
    )
    client = MagicMock()
    client.get_schema.return_value = {
        "fields": [{"field_id": "owner.vehicles", "items": {"sort": "Enum", "enum_values": ["ford", "toyota"]}}]
    }
    client.get_value_space.return_value = {"members": ["toyota", "ford"]}

    generate_cmd._report_field_diff(
        client,
        "rs_1",
        project,
        value_spaces_resolved={"owner.vehicles": {"space": "acme/vehicle-makes", "version": 1, "space_id": "vs_1"}},
    )

    out = " ".join(capsys.readouterr().out.split())
    assert "members verified" in out, out
    assert "differ" not in out, out


def test_fields_pull_keeps_a_local_collection_declaration(tmp_path, monkeypatch):
    """A pulled row carries no item shape; overwriting a collection with ``string`` broke the file."""
    from unittest.mock import patch

    from typer.testing import CliRunner

    from aethis_cli.main import app
    from tests.test_fields_cmd import _cfg

    monkeypatch.chdir(tmp_path)
    _project(
        tmp_path,
        "fields:\n  - {key: owner.vehicles, type: collection, value_space: acme/vehicle-makes, items: {sort: Enum}}\n",
    )
    client = MagicMock()
    client.get_schema.return_value = {"fields": [{"field_id": "owner.vehicles", "field_type": "collection"}]}
    with (
        patch("aethis_cli.commands.fields_cmd.load_project_config", return_value=_cfg(tmp_path)),
        patch("aethis_cli.commands.fields_cmd.resolve_api_key", return_value="ak"),
        patch("aethis_cli.commands.fields_cmd.make_authed_client", return_value=client),
    ):
        result = CliRunner().invoke(app, ["fields", "pull", "-b", "rs_1"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    parsed = generate_cmd._parse_fields_yaml(tmp_path / "fields" / "fields.yaml")
    assert parsed["owner.vehicles"]["type"] == "collection"
    assert parsed["owner.vehicles"]["items"] == {"sort": "Enum"}
    assert generate_cmd.validate_fields_list(list(parsed.values())) == []
