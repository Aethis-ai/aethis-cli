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
