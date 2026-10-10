from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx
import typer
import yaml

from aethis_cli.client import AethisClient
from aethis_cli.commands import generate_cmd


@pytest.mark.parametrize(
    "computed",
    [
        {"op": "any_true", "fields": ["craft.a", "craft.b"]},
        {"op": "any_in", "collection": "craft.items", "values": ["ion"]},
    ],
)
def test_lossless_computation_upload(tmp_path: Path, computed: dict[str, Any]) -> None:
    fields = [{"key": "craft.aggregate", "type": "bool", "computed": computed}]
    generate_cmd._write_fields_yaml(tmp_path / "fields.yaml", {"craft.aggregate": fields[0]})
    assert yaml.safe_load((tmp_path / "fields.yaml").read_text())["fields"] == fields
    (tmp_path / "fields").mkdir()
    (tmp_path / "fields" / "fields.yaml").write_text(yaml.safe_dump({"fields": fields}))
    client = MagicMock()
    client.expected_field_spec_properties.return_value = {"computed"}
    client.expected_field_computation_operations.return_value = {"any_true", "any_in"}
    generate_cmd._upload_field_vocabulary(client, "project", tmp_path)
    assert client.set_field_spec.call_args.args[1] == [{"key": "craft.aggregate", "sort": "bool", "computed": computed}]


@pytest.mark.parametrize("operations", [None, set(), {"any_in"}])
def test_unsupported_computation_refused_before_mutation(tmp_path: Path, operations: set[str] | None) -> None:
    (tmp_path / "fields").mkdir()
    (tmp_path / "fields" / "fields.yaml").write_text(
        yaml.safe_dump(
            {
                "fields": [
                    {"key": "craft.aggregate", "type": "bool", "computed": {"op": "any_true", "fields": ["craft.a"]}}
                ]
            }
        )
    )
    client = MagicMock()
    client.expected_field_spec_properties.return_value = {"computed"}
    client.expected_field_computation_operations.return_value = operations
    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "project", tmp_path)
    client.set_field_spec.assert_not_called()
    client.add_guidance.assert_not_called()


@respx.mock
def test_operations_follow_only_expected_field_computation_union() -> None:
    schemas = {
        "ExpectedFieldSpec": {
            "properties": {
                "computed": {
                    "anyOf": [
                        {
                            "oneOf": [
                                {"$ref": "#/components/schemas/BooleanAggregateDeclaration"},
                                {"$ref": "#/components/schemas/ComputedDeclaration"},
                            ]
                        },
                        {"type": "null"},
                    ]
                }
            }
        },
        "BooleanAggregateDeclaration": {"properties": {"op": {"const": "any_true"}}},
        "ComputedDeclaration": {"properties": {"op": {"enum": ["any_in"]}}},
        "Unrelated": {"properties": {"op": {"const": "unrelated"}}},
    }
    respx.get("https://example.com/openapi.json").mock(
        return_value=httpx.Response(200, json={"components": {"schemas": schemas}})
    )
    client = AethisClient(base_url="https://example.com")
    assert client.expected_field_computation_operations() == {"any_true", "any_in"}


@pytest.mark.parametrize(
    "body,status,expected",
    [
        ({"components": {"schemas": {"ExpectedFieldSpec": {"properties": {}}}}}, 200, set()),
        ({}, 200, None),
        ({}, 503, None),
    ],
)
@respx.mock
def test_unavailable_or_old_engine_capability(body: dict[str, Any], status: int, expected: set[str] | None) -> None:
    respx.get("https://example.com/openapi.json").mock(return_value=httpx.Response(status, json=body))
    client = AethisClient(base_url="https://example.com")
    assert client.expected_field_computation_operations() == expected


def test_generate_refuses_unsupported_computation_before_project_mutation(tmp_path, monkeypatch) -> None:
    from tests.test_generate_no_publish import _project, _engine, _wire, SUCCESS

    _project(tmp_path)
    (tmp_path / "fields" / "fields.yaml").write_text(
        yaml.safe_dump(
            {
                "fields": [
                    {"key": "craft.a", "type": "bool"},
                    {"key": "craft.aggregate", "type": "bool", "computed": {"op": "any_true", "fields": ["craft.a"]}},
                ]
            }
        )
    )
    client = _engine(SUCCESS)
    client.expected_field_computation_operations.return_value = {"any_in"}
    _wire(monkeypatch, tmp_path, client)
    with pytest.raises(typer.Exit):
        generate_cmd._run_generate(
            project_id="p", mode="refine", extra_hint="Fix aggregate", poll=False, timeout=30, no_publish=True
        )
    for method in ("upload_sources", "add_guidance", "create_project", "set_field_spec", "generate"):
        getattr(client, method).assert_not_called()


@pytest.mark.parametrize("referenced", [False, True])
def test_generate_transports_collection_and_computation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, referenced: bool
) -> None:
    from tests.test_generate_no_publish import _project, _engine, _wire, SUCCESS

    _project(tmp_path)
    collection = {
        "key": "craft.parts",
        "type": "collection",
        "items": {"sort": "Enum", "max_items": 10, "completion_question": "Is that every part?"},
        "enum_labels": {"ion": "Ion drive"},
    }
    if referenced:
        collection["value_space"] = "parts"
    else:
        collection["items"]["enum_values"] = ["ion"]
    computed = {
        "key": "craft.has_ion",
        "type": "bool",
        "computed": {"op": "any_in", "collection": "craft.parts", "values": ["ion"]},
    }
    fields = [collection, computed]
    (tmp_path / "fields" / "fields.yaml").write_text(yaml.safe_dump({"fields": fields}))
    client = _engine(SUCCESS)
    client.expected_field_computation_operations.return_value = {"any_in"}
    client.expected_field_spec_properties.return_value = {"items", "computed", "enum_labels", "value_space"}
    _wire(monkeypatch, tmp_path, client)
    synced = MagicMock()
    monkeypatch.setattr(generate_cmd, "_sync_value_spaces", synced)
    generate_cmd._run_generate(
        project_id="p", mode="refine", extra_hint="Refine", poll=False, timeout=30, no_publish=True
    )
    expected = [{**{k: v for k, v in f.items() if k != "type"}, "sort": f["type"]} for f in fields]
    assert client.set_field_spec.call_args.args[1] == expected
    assert synced.call_count == int(referenced)
    output = tmp_path / "roundtrip.yaml"
    generate_cmd._write_fields_yaml(output, {f["key"]: f for f in fields})
    assert yaml.safe_load(output.read_text())["fields"] == fields


@pytest.mark.parametrize(
    "items,space",
    [
        (None, None),
        ({"sort": "Bool", "enum_values": ["ion"]}, None),
        ({"sort": "Enum"}, None),
        ({"sort": "Enum", "enum_values": ["ion"]}, "parts"),
    ],
)
def test_invalid_collection_shape_is_rejected(items: Any, space: str | None) -> None:
    field = {"key": "craft.parts", "type": "collection", "items": items}
    if space:
        field["value_space"] = space
    assert generate_cmd.validate_fields_list([field])


def test_collection_label_must_name_inline_item() -> None:
    assert generate_cmd.validate_fields_list(
        [
            {
                "key": "craft.parts",
                "type": "collection",
                "items": {"sort": "Enum", "enum_values": ["ion"]},
                "enum_labels": {"unknown": "Unknown"},
            }
        ]
    )


@pytest.mark.parametrize("item_sort", ["enum", "ENUM", " Enum "])
def test_generate_refuses_noncanonical_collection_item_sort_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, item_sort: str
) -> None:
    from tests.test_generate_no_publish import _project, _engine, _wire, SUCCESS

    _project(tmp_path)
    fields = [{"key": "craft.parts", "type": "collection", "items": {"sort": item_sort, "enum_values": ["ion"]}}]
    (tmp_path / "fields" / "fields.yaml").write_text(yaml.safe_dump({"fields": fields}))
    client = _engine(SUCCESS)
    client.expected_field_spec_properties.return_value = {"items"}
    _wire(monkeypatch, tmp_path, client)
    with pytest.raises(typer.Exit):
        generate_cmd._run_generate(
            project_id="p", mode="refine", extra_hint="Refine", poll=False, timeout=30, no_publish=True
        )
    for method in ("upload_sources", "add_guidance", "create_project", "set_field_spec", "generate"):
        getattr(client, method).assert_not_called()


@pytest.mark.parametrize("properties", [None, {"computed", "enum_labels"}])
def test_generate_refuses_collection_before_mutation_when_items_not_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, properties: set[str] | None
) -> None:
    from tests.test_generate_no_publish import _project, _engine, _wire, SUCCESS

    _project(tmp_path)
    fields = [{"key": "craft.parts", "type": "collection", "items": {"sort": "Enum", "enum_values": ["ion"]}}]
    (tmp_path / "fields" / "fields.yaml").write_text(yaml.safe_dump({"fields": fields}))
    client = _engine(SUCCESS)
    client.expected_field_spec_properties.return_value = properties
    _wire(monkeypatch, tmp_path, client)
    with pytest.raises(typer.Exit):
        generate_cmd._run_generate(
            project_id="p", mode="refine", extra_hint="Refine", poll=False, timeout=30, no_publish=True
        )
    for method in ("upload_sources", "add_guidance", "create_project", "set_field_spec", "generate"):
        getattr(client, method).assert_not_called()
