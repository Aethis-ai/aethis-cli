"""`display_name:` in aethis.yaml is sent on `aethis generate` / `aethis refine`.

Before this, the key was sent only on `aethis publish`. A project generated
with `--no-publish` (then promoted by copying the stored ruleset) kept the
engine's default name derived from its section id.

The engine silently ignores a body key it does not know, so a name sent to
an older engine would be dropped without a word. The CLI therefore checks
the engine advertises the `name` generation control before any project
mutation, the same capability gate `--thinking` uses.
"""

from __future__ import annotations

import json

import pytest
import respx
import typer

from aethis_cli.client import AethisClient
from aethis_cli.commands import generate_cmd
from tests.test_generate_no_publish import SUCCESS, _engine, _project, _wire

BASE = "https://test.local"
LABEL = "English language"


def _schema(*props: str) -> dict:
    return {"components": {"schemas": {"GenerationModeRequest": {"properties": {p: {} for p in props}}}}}


# --- client -----------------------------------------------------------------


@respx.mock
def test_client_sends_name_in_generate_body():
    respx.get(f"{BASE}/openapi.json").respond(200, json=_schema("mode", "name"))
    route = respx.post(f"{BASE}/api/v1/public/projects/p/generate").respond(202, json={})
    with AethisClient("ak", BASE) as client:
        client.generate("p", mode="fresh", name=LABEL)
    assert json.loads(route.calls.last.request.content) == {"mode": "fresh", "name": LABEL}


@respx.mock
def test_client_body_unchanged_without_name():
    schema = respx.get(f"{BASE}/openapi.json").respond(200, json=_schema("mode", "name"))
    route = respx.post(f"{BASE}/api/v1/public/projects/p/generate").respond(202, json={})
    with AethisClient("ak", BASE) as client:
        client.generate("p")
    assert route.calls.last.request.content == b""
    # No name, no capability probe: unchanged behaviour against any engine.
    assert not schema.called


@respx.mock
def test_client_refuses_name_on_engine_without_the_control():
    respx.get(f"{BASE}/openapi.json").respond(200, json=_schema("mode"))
    post = respx.post(f"{BASE}/api/v1/public/projects/p/generate").respond(202, json={})
    with AethisClient("ak", BASE) as client:
        with pytest.raises(ValueError, match="no generation was started"):
            client.generate("p", name=LABEL)
    assert not post.called


# --- command ----------------------------------------------------------------


def _wire_with_name(monkeypatch, tmp_path, client, display_name):
    _wire(monkeypatch, tmp_path, client)
    cfg = generate_cmd.load_project_config()
    cfg.display_name = display_name
    monkeypatch.setattr(generate_cmd, "load_project_config", lambda: cfg)


@pytest.mark.parametrize("mode", ["fresh", "refine"])
def test_display_name_reaches_generate_in_both_modes(tmp_path, monkeypatch, mode):
    _project(tmp_path)
    client = _engine(SUCCESS)
    client.generation_mode_request_properties.return_value = {"mode", "name"}
    _wire_with_name(monkeypatch, tmp_path, client, LABEL)
    generate_cmd._run_generate(project_id="p", poll=False, timeout=30, mode=mode, no_publish=True)
    assert client.generate.call_args.kwargs["name"] == LABEL


def test_absent_display_name_sends_no_name(tmp_path, monkeypatch):
    _project(tmp_path)
    client = _engine(SUCCESS)
    _wire_with_name(monkeypatch, tmp_path, client, None)
    generate_cmd._run_generate(project_id="p", poll=False, timeout=30, no_publish=True)
    assert client.generate.call_args.kwargs.get("name") is None


def test_old_engine_is_refused_before_any_project_mutation(tmp_path, monkeypatch):
    _project(tmp_path)
    client = _engine(SUCCESS)
    client.generation_mode_request_properties.return_value = {"mode"}
    _wire_with_name(monkeypatch, tmp_path, client, LABEL)
    with pytest.raises(typer.Exit) as rejected:
        generate_cmd._run_generate(project_id="p", poll=False, timeout=30, no_publish=True)
    assert rejected.value.exit_code == 1
    client.get_project.assert_not_called()
    client.create_project.assert_not_called()
    client.upload_sources.assert_not_called()
    client.generate.assert_not_called()
