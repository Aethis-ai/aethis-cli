"""Contract-v1 test upload is strict and cannot be silently downgraded."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import respx
import typer

from aethis_cli.client import AethisClient
from aethis_cli.commands import generate_cmd

BASE = "http://engine.test"
WIRE_REQUEST = Path(__file__).parent / "fixtures" / "acceptance_contract_v1_wire_request.json"


def _write_contract(tmp_path, content: dict) -> object:
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(content))
    return path


def _contract(*, bindings: object = None, include_bindings: bool = False) -> dict:
    value: dict = {
        "contract_version": 1,
        "test_cases": [
            {
                "name": "unknown answer",
                "field_values": {"field": "value"},
                "expected_outcome": "undetermined",
                "expectations": {
                    "pending_reviews": {"resolution_fields": ["field"], "unmapped_count": 0},
                    "useful_unknown_fields": ["field"],
                },
            }
        ],
    }
    if include_bindings:
        value["expected_review_bindings"] = bindings
    return value


def _openapi(*, contract: bool) -> dict:
    props = {"test_cases": {}, "replace": {}}
    if contract:
        props.update({"contract_version": {}, "expected_review_bindings": {}})
    return {"components": {"schemas": {"AddTestCaseRequest": {"properties": props}}}}


def _wire_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client: MagicMock) -> None:
    (tmp_path / "sources").mkdir(exist_ok=True)
    (tmp_path / "sources" / "policy.md").write_text("policy", encoding="utf-8")
    cfg = SimpleNamespace(config_path=tmp_path, base_url=BASE, project_id=None, project="policy")
    monkeypatch.setattr(generate_cmd, "load_project_config", lambda: cfg)
    monkeypatch.setattr(generate_cmd, "resolve_api_key", lambda _cfg: "key")
    monkeypatch.setattr(generate_cmd, "resolve_anthropic_key", lambda _cfg: None)
    monkeypatch.setattr(generate_cmd, "make_authed_client", lambda *_args, **_kwargs: client)


def _assert_only_capability_probe(client: MagicMock) -> None:
    """A rejected local plan may probe support, but must make no other call."""
    allowed = {"supports_test_acceptance_contract", "supports_test_replace"}
    assert [call for call in client.method_calls if call[0] not in allowed] == []


@respx.mock(base_url=BASE)
def test_sidecar_sends_full_atomic_envelope_and_verifies_readback(respx_mock, tmp_path):
    contract = _contract(bindings={"field": {"token": False}}, include_bindings=True)
    path = _write_contract(tmp_path, contract)
    body: dict = {}
    respx_mock.get("/openapi.json").mock(return_value=httpx.Response(200, json=_openapi(contract=True)))

    def post(request: httpx.Request) -> httpx.Response:
        body.update(json.loads(request.content))
        return httpx.Response(201, json={"added": 1, "replaced": 4})

    respx_mock.post("/api/v1/public/projects/proj/tests").mock(side_effect=post)
    expected = generate_cmd._load_acceptance_contract(path)
    respx_mock.get("/api/v1/public/projects/proj").mock(
        return_value=httpx.Response(
            200,
            json={
                "authoring_acceptance_contract_version": 1,
                "expected_review_bindings": contract["expected_review_bindings"],
                "authoring_acceptance_contract_digest": generate_cmd._acceptance_contract_digest(expected),
            },
        )
    )

    with AethisClient("key", BASE) as client:
        generate_cmd._upload_test_cases(client, "proj", tmp_path, acceptance_contract_path=path)

    assert body["replace"] is True
    assert body["contract_version"] == 1
    assert body["expected_review_bindings"] == {"field": {"token": False}}
    assert body["test_cases"][0]["expectations"]["pending_reviews"]["unmapped_count"] == 0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c.update({"contract_version": True}), "contract_version"),
        (lambda c: c.update({"expected_review_bindings": None}), "null"),
        (
            lambda c: c["test_cases"][0]["expectations"]["pending_reviews"].update({"unmapped_count": False}),
            "not a boolean",
        ),
        (lambda c: c["test_cases"][0].update({"extra": 1}), "unsupported"),
    ],
)
def test_sidecar_rejects_strict_malformed_values(tmp_path, mutate, message):
    contract = _contract()
    mutate(contract)
    path = _write_contract(tmp_path, contract)
    with pytest.raises(generate_cmd.AcceptanceContractError, match=message):
        generate_cmd._load_acceptance_contract(path)


def test_empty_binding_catalogue_is_valid_and_distinct_from_null(tmp_path):
    contract = _contract(bindings={}, include_bindings=True)
    contract["test_cases"][0].pop("expectations")
    path = _write_contract(tmp_path, contract)
    loaded = generate_cmd._load_acceptance_contract(path)
    assert loaded["expected_review_bindings"] == {}


@respx.mock(base_url=BASE)
def test_older_engine_stops_before_replacement(respx_mock, tmp_path):
    path = _write_contract(tmp_path, _contract())
    respx_mock.get("/openapi.json").mock(return_value=httpx.Response(200, json=_openapi(contract=False)))
    with AethisClient("key", BASE) as client:
        with pytest.raises(generate_cmd.AcceptanceContractError, match="before replacing"):
            generate_cmd._upload_test_cases(client, "proj", tmp_path, acceptance_contract_path=path)


@respx.mock(base_url=BASE)
def test_wrong_readback_digest_stops_before_generation(respx_mock, tmp_path):
    path = _write_contract(tmp_path, _contract())
    respx_mock.get("/openapi.json").mock(return_value=httpx.Response(200, json=_openapi(contract=True)))
    respx_mock.post("/api/v1/public/projects/proj/tests").mock(return_value=httpx.Response(201, json={"added": 1}))
    respx_mock.get("/api/v1/public/projects/proj").mock(
        return_value=httpx.Response(
            200,
            json={
                "authoring_acceptance_contract_version": 1,
                "expected_review_bindings": None,
                "authoring_acceptance_contract_digest": "sha256:old-engine-dropped-fields",
            },
        )
    )
    with AethisClient("key", BASE) as client:
        with pytest.raises(generate_cmd.AcceptanceContractError, match="exact acceptance-contract"):
            generate_cmd._upload_test_cases(client, "proj", tmp_path, acceptance_contract_path=path)


@pytest.mark.parametrize("unsafe_value", [9007199254740992, float("nan")])
@respx.mock(base_url=BASE)
def test_noncanonical_value_stops_before_remote_mutation(respx_mock, tmp_path, unsafe_value):
    contract = _contract()
    contract["test_cases"][0]["field_values"]["unsafe"] = unsafe_value
    path = _write_contract(tmp_path, contract)

    with AethisClient("key", BASE) as client:
        with pytest.raises(generate_cmd.AcceptanceContractError, match="canonical JSON domain"):
            generate_cmd._upload_test_cases(client, "proj", tmp_path, acceptance_contract_path=path)

    assert not respx_mock.calls


@pytest.mark.parametrize(
    "readback_override",
    [
        {"authoring_acceptance_contract_version": True},
        {"expected_review_bindings": {"field": {"token": 0}}},
    ],
)
@respx.mock(base_url=BASE)
def test_readback_comparison_rejects_bool_int_type_coercion(respx_mock, tmp_path, readback_override):
    contract = _contract(bindings={"field": {"token": False}}, include_bindings=True)
    path = _write_contract(tmp_path, contract)
    normalised = generate_cmd._load_acceptance_contract(path)
    readback = {
        "authoring_acceptance_contract_version": 1,
        "expected_review_bindings": contract["expected_review_bindings"],
        "authoring_acceptance_contract_digest": generate_cmd._acceptance_contract_digest(normalised),
    }
    readback.update(readback_override)
    respx_mock.get("/openapi.json").mock(return_value=httpx.Response(200, json=_openapi(contract=True)))
    respx_mock.post("/api/v1/public/projects/proj/tests").mock(return_value=httpx.Response(201, json={"added": 1}))
    respx_mock.get("/api/v1/public/projects/proj").mock(return_value=httpx.Response(200, json=readback))

    with AethisClient("key", BASE) as client:
        with pytest.raises(generate_cmd.AcceptanceContractError, match="exact acceptance-contract"):
            generate_cmd._upload_test_cases(client, "proj", tmp_path, acceptance_contract_path=path)


def test_wire_contract_has_independent_fixed_digest():
    request = json.loads(WIRE_REQUEST.read_text())
    contract = {
        "contract_version": request["contract_version"],
        "test_cases": request["test_cases"],
        "expected_review_bindings": request["expected_review_bindings"],
    }

    assert generate_cmd._acceptance_contract_digest(contract) == (
        "sha256:eb0436a6575f0fa34a162f70672982a6ece61003fb627cb63c7dfcf7f02f8535"
    )


def test_composed_and_decomposed_contract_values_have_distinct_digests():
    composed = _contract(bindings={"caf\u00e9": {"tok\u00e9n": True}}, include_bindings=True)
    decomposed = _contract(bindings={"cafe\u0301": {"toke\u0301n": True}}, include_bindings=True)
    composed["test_cases"][0]["expectations"]["pending_reviews"]["resolution_fields"] = ["caf\u00e9"]
    decomposed["test_cases"][0]["expectations"]["pending_reviews"]["resolution_fields"] = ["cafe\u0301"]
    assert generate_cmd._acceptance_contract_digest(composed) != generate_cmd._acceptance_contract_digest(decomposed)


def test_scenarios_yaml_uses_expect_advanced_keys_without_a_silent_drop(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "scenarios.yaml").write_text(
        """tests:
  - name: review required
    inputs: {field: value}
    expect:
      outcome: undetermined
      pending_reviews: {resolution_fields: [field], unmapped_count: 0}
      useful_unknown_fields: [field]
"""
    )
    raw = generate_cmd.yaml.load((tests / "scenarios.yaml").read_text(), Loader=generate_cmd._UniqueKeyLoader)
    cases = generate_cmd._normalise_test_cases(raw["tests"], "tests", yaml_shape=True)
    assert cases[0]["expectations"] == {
        "pending_reviews": {"resolution_fields": ["field"], "unmapped_count": 0},
        "useful_unknown_fields": ["field"],
    }


def test_scenarios_yaml_duplicate_key_is_rejected(tmp_path):
    path = tmp_path / "scenarios.yaml"
    path.write_text("tests: []\ntests: []\n")
    with pytest.raises(generate_cmd.yaml.YAMLError, match="duplicate key"):
        generate_cmd.yaml.load(path.read_text(), Loader=generate_cmd._UniqueKeyLoader)


def test_missing_contract_path_is_an_acceptance_error(tmp_path):
    missing = tmp_path / "missing.json"
    with pytest.raises(generate_cmd.AcceptanceContractError, match="cannot read acceptance contract"):
        generate_cmd._load_acceptance_contract(missing)


def test_contract_and_scenarios_require_utf8(tmp_path):
    contract = tmp_path / "contract.json"
    contract.write_bytes(b'{"contract_version":1,"test_cases":[{"name":"\xff"}]}')
    with pytest.raises(generate_cmd.AcceptanceContractError, match="must be UTF-8"):
        generate_cmd._load_acceptance_contract(contract)

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "scenarios.yaml").write_bytes(b"tests:\n  - name: \xff\n")
    with pytest.raises(generate_cmd.AcceptanceContractError, match="must be UTF-8"):
        generate_cmd._load_scenarios_contract(tmp_path)


def test_explicit_null_expectations_is_valid_no_assertion(tmp_path):
    contract = _contract()
    contract["test_cases"][0]["expectations"] = None
    loaded = generate_cmd._load_acceptance_contract(_write_contract(tmp_path, contract))
    assert "expectations" not in loaded["test_cases"][0]


def test_legacy_yaml_merges_and_nonsemantic_metadata_remain_compatible(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "scenarios.yaml").write_text(
        """suite_description: retained author notes
defaults: &defaults
  age: 30
  income: 100
tests:
  - name: merged legacy case
    description: this never belonged to the API payload
    labels: [regression]
    inputs:
      <<: *defaults
      income: 5
    expect: {outcome: eligible}
""",
        encoding="utf-8",
    )
    client = MagicMock()
    client.supports_test_replace.return_value = True

    prepared = generate_cmd._prepare_test_upload(client, tmp_path)

    assert prepared is not None
    assert prepared.materialise()["test_cases"] == [
        {
            "name": "merged legacy case",
            "field_values": {"age": 30, "income": 5},
            "expected_outcome": "eligible",
        }
    ]


def test_unknown_expect_key_is_rejected_as_a_possible_assertion(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "scenarios.yaml").write_text(
        """tests:
  - name: typo
    inputs: {}
    expect: {outcome: eligible, useful_unknown_field: [field]}
""",
        encoding="utf-8",
    )
    with pytest.raises(generate_cmd.AcceptanceContractError, match="expect may contain only"):
        generate_cmd._load_scenarios_contract(tmp_path)


def test_prepared_contract_is_the_immutable_payload_later_uploaded(tmp_path):
    path = _write_contract(tmp_path, _contract())
    client = MagicMock()
    client.supports_test_acceptance_contract.return_value = True
    prepared = generate_cmd._prepare_test_upload(client, tmp_path, acceptance_contract_path=path)
    assert prepared is not None
    expected = prepared.materialise()
    with pytest.raises(TypeError):
        prepared.contract["contract_version"] = 2  # type: ignore[index]
    client.add_tests.return_value = {"added": 1, "replaced": 0}
    client.get_project.return_value = {
        "authoring_acceptance_contract_version": 1,
        "expected_review_bindings": None,
        "authoring_acceptance_contract_digest": prepared.digest,
    }

    path.write_text('{"contract_version":2,"test_cases":[]}', encoding="utf-8")
    generate_cmd._upload_prepared_test_cases(client, "proj", prepared)

    assert client.add_tests.call_args.args[1] == expected["test_cases"]
    client.supports_test_acceptance_contract.assert_called_once_with()


@pytest.mark.parametrize("case", ["invalid", "missing", "non_utf8", "unsupported"])
def test_run_rejects_bad_contract_before_any_mutation(tmp_path, monkeypatch, case):
    client = MagicMock()
    _wire_run(monkeypatch, tmp_path, client)
    path = tmp_path / "acceptance.json"
    if case == "invalid":
        path.write_text('{"contract_version":2,"test_cases":[]}', encoding="utf-8")
    elif case == "non_utf8":
        path.write_bytes(b'{"contract_version":1,"test_cases":[{"name":"\xff"}]}')
    elif case == "unsupported":
        path = _write_contract(tmp_path, _contract())
        client.supports_test_acceptance_contract.return_value = False

    with pytest.raises(typer.Exit) as raised:
        generate_cmd._run_generate(
            project_id=None,
            poll=False,
            timeout=1,
            mode="refine",
            extra_hint="must never be appended",
            acceptance_contract=path,
        )

    assert raised.value.exit_code == 1
    _assert_only_capability_probe(client)
    if case == "unsupported":
        client.supports_test_acceptance_contract.assert_called_once_with()
    else:
        client.supports_test_acceptance_contract.assert_not_called()


@pytest.mark.parametrize("case", ["invalid", "non_utf8", "unsupported"])
def test_run_rejects_bad_scenarios_before_any_mutation(tmp_path, monkeypatch, case):
    client = MagicMock()
    _wire_run(monkeypatch, tmp_path, client)
    tests = tmp_path / "tests"
    tests.mkdir()
    if case == "invalid":
        body = """tests:
  - name: typo
    inputs: {}
    expect: {outcome: eligible, unknown_assertion: true}
"""
    elif case == "non_utf8":
        body = None
        (tests / "scenarios.yaml").write_bytes(b"tests:\n  - name: \xff\n")
    else:
        body = """tests:
  - name: review
    inputs: {}
    expect:
      outcome: undetermined
      pending_reviews: {resolution_fields: [], unmapped_count: 0}
"""
        client.supports_test_acceptance_contract.return_value = False
    if body is not None:
        (tests / "scenarios.yaml").write_text(body, encoding="utf-8")

    with pytest.raises(typer.Exit) as raised:
        generate_cmd._run_generate(
            project_id=None,
            poll=False,
            timeout=1,
            mode="refine",
            extra_hint="must never be appended",
        )

    assert raised.value.exit_code == 1
    _assert_only_capability_probe(client)
    if case == "unsupported":
        client.supports_test_acceptance_contract.assert_called_once_with()
    else:
        client.supports_test_acceptance_contract.assert_not_called()
