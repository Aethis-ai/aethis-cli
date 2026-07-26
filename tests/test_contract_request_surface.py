"""The CLI only sends request surface the API actually defines.

The API rejects an unknown top-level `/decide` member with a 422 instead of
ignoring it, so a caller can never mistake unimplemented surface for
supported surface. The CLI's half of that bargain: never send one, and render
the server's rejection in terms a developer can act on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aethis_cli import contract
from aethis_cli.client import AethisClient
from aethis_cli.output import format_error_detail

FIXTURES = Path(__file__).parent / "fixtures" / "contract"


def test_declared_request_keys_match_the_engine_contract():
    """Every option the CLI is willing to send is a member the API defines.

    Pinned against the OpenAPI contract fixture copied from aethis-core, so a
    request member added or removed upstream shows up here rather than as a
    422 in a user's terminal.
    """
    contract_fixture = json.loads((FIXTURES / "engine_explain_source_contract.json").read_text())
    assert contract_fixture["decide"]["additional_properties"] is False
    assert contract_fixture["decide"]["include_explanation_in_body"] is True
    assert "include_explanation" not in contract_fixture["decide"]["query_parameters"]
    assert "include_explanation" in contract.DECIDE_REQUEST_KEYS


def test_undeclared_option_is_refused_locally(monkeypatch):
    client = AethisClient(api_key=None, base_url="https://example.invalid", unsigned=True)
    with pytest.raises(contract.UnsupportedRequestOption) as excinfo:
        client.decide("some-ruleset:20260101-abcdef12", {"a": 1}, batch=[{"field_values": {}}])
    message = str(excinfo.value)
    assert "batch" in message
    assert "include_explanation" in message  # the supported set is named


def test_undeclared_option_never_reaches_the_wire(monkeypatch):
    client = AethisClient(api_key=None, base_url="https://example.invalid", unsigned=True)
    calls = []
    monkeypatch.setattr(client, "_request", lambda *a, **kw: calls.append((a, kw)) or {})
    with pytest.raises(contract.UnsupportedRequestOption):
        client.decide("some-ruleset:20260101-abcdef12", {"a": 1}, batch=[])
    assert calls == []


def test_declared_options_pass_through(monkeypatch):
    client = AethisClient(api_key=None, base_url="https://example.invalid", unsigned=True)
    seen = {}

    def _fake(method, path, **kwargs):
        seen.update({"method": method, "path": path, **kwargs})
        return {}

    monkeypatch.setattr(client, "_request", _fake)
    client.decide("rs:20260101-abcdef12", {"a": 1}, include_explanation=True, include_trace=True)
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/public/decide"
    assert seen["json"]["include_explanation"] is True
    assert set(seen["json"]) <= contract.DECIDE_REQUEST_KEYS


def test_server_validation_envelope_renders_readably():
    """The captured 422 for a fictional `batch` member."""
    payload = json.loads((FIXTURES / "decide_422_unknown_request_key.json").read_text())
    rendered = format_error_detail(payload["detail"])
    assert "batch" in rendered
    assert "does not accept it" in rendered
    assert "[{'field_values'" not in rendered  # not a raw list repr
