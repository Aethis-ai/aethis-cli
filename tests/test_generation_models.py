"""Per-generation model and credential boundaries."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from typer.testing import CliRunner

from aethis_cli.client import AethisClient, GenerationModel, normalize_thinking
from aethis_cli.commands import generate_cmd, refine_cmd
from aethis_cli.config import load_project_config, resolve_deepseek_key
from aethis_cli.main import app

BASE = "https://test.local"


@pytest.mark.parametrize("mode", ["fresh", "refine"])
@respx.mock
def test_deepseek_generation_header_is_request_scoped(mode: str) -> None:
    generate = respx.post(f"{BASE}/api/v1/public/projects/p/generate").respond(202, json={"job_id": "j"})
    discover = respx.post(f"{BASE}/api/v1/public/projects/p/fields/discover").respond(200, json={})
    review = respx.post(f"{BASE}/api/v1/public/projects/p/review").respond(200, json={})
    with AethisClient("ak", BASE) as client:
        client.generate("p", mode=mode, model=GenerationModel.deepseek, deepseek_key="secret")
        client.discover_fields("p")
        client.review("p", coach=True)
    request = generate.calls.last.request
    assert json.loads(request.content) == {"mode": mode, "model": "deepseek-flash"}
    assert request.headers["X-DeepSeek-Key"] == "secret"
    assert "X-Anthropic-Key" not in request.headers
    for route in (discover, review):
        assert "X-DeepSeek-Key" not in route.calls.last.request.headers


@respx.mock
def test_explicit_sonnet_keeps_anthropic_transport() -> None:
    route = respx.post(f"{BASE}/api/v1/public/projects/p/generate").respond(202, json={})
    with AethisClient("ak", BASE, anthropic_key="anthropic") as client:
        client.generate("p", model=GenerationModel.sonnet)
    assert json.loads(route.calls.last.request.content) == {"model": "claude-sonnet-5"}
    assert route.calls.last.request.headers["X-Anthropic-Key"] == "anthropic"
    assert "X-DeepSeek-Key" not in route.calls.last.request.headers


def test_client_rejects_provider_key_mix() -> None:
    with AethisClient("ak", BASE, anthropic_key="anthropic") as client:
        with pytest.raises(ValueError, match="without an Anthropic"):
            client.generate("p", model=GenerationModel.deepseek, deepseek_key="secret")
    with AethisClient("ak", BASE) as client:
        with pytest.raises(ValueError, match="requires model"):
            client.generate("p", deepseek_key="secret")


@respx.mock
def test_thinking_serialization_preserves_omission_and_explicit_null() -> None:
    respx.get(f"{BASE}/openapi.json").respond(
        200, json={"components": {"schemas": {"GenerationModeRequest": {"properties": {"thinking": {}}}}}}
    )
    route = respx.post(f"{BASE}/api/v1/public/projects/p/generate").respond(202, json={})
    with AethisClient("ak", BASE) as client:
        client.generate("p")
        assert route.calls.last.request.content == b""
        client.generate("p", thinking=None)
        assert json.loads(route.calls.last.request.content) == {"thinking": None}
        client.generate("p", thinking="enabled:48000")
    assert json.loads(route.calls.last.request.content) == {"thinking": "enabled:48000"}


def test_thinking_normalization_bounds_significant_digits() -> None:
    assert normalize_thinking("  enabled:00001024 ") == "enabled:1024"
    assert normalize_thinking("enabled:000000000009999999999") == "enabled:9999999999"
    with pytest.raises(ValueError, match="invalid_thinking"):
        normalize_thinking("enabled:10000000000")
    with pytest.raises(ValueError, match="invalid_thinking"):
        normalize_thinking("enabled:10_24")


@pytest.mark.parametrize("command", ["generate", "refine"])
def test_flags_forward_model(command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run = MagicMock()
    module = generate_cmd if command == "generate" else refine_cmd
    monkeypatch.setattr(module, "_run_generate", run)
    result = CliRunner().invoke(app, [command, "--model", "deepseek-flash", "--no-poll"])
    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["model"] == GenerationModel.deepseek
    invalid = CliRunner().invoke(app, [command, "--model", "unknown"])
    assert invalid.exit_code == 2
    assert run.call_count == 1


@pytest.mark.parametrize("command", ["generate", "refine"])
def test_flags_forward_thinking(command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run = MagicMock()
    module = generate_cmd if command == "generate" else refine_cmd
    monkeypatch.setattr(module, "_run_generate", run)
    result = CliRunner().invoke(app, [command, "--thinking", "enabled:48000", "--no-poll"])
    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["thinking"] == "enabled:48000"


def test_thinking_warning_is_rendered(capsys: pytest.CaptureFixture[str]) -> None:
    generate_cmd._render_thinking_warnings(
        {
            "authoring_config": {
                "warnings": [{"code": "thinking_budget_ignored", "message": "budget does not bound reasoning"}]
            }
        }
    )
    assert "thinking_budget_ignored" in capsys.readouterr().out


def test_thinking_capability_rejection_precedes_project_mutation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_generate_no_publish import _engine, _project, _wire

    _project(tmp_path)
    client = _engine({})
    client.generation_mode_request_properties.return_value = set()
    _wire(monkeypatch, tmp_path, client)
    with pytest.raises(__import__("typer").Exit) as rejected:
        generate_cmd._run_generate(project_id="p", poll=False, timeout=30, thinking="disabled")
    assert rejected.value.exit_code == 1
    client.get_project.assert_not_called()
    client.create_project.assert_not_called()


def test_configurable_deepseek_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "aethis.yaml"
    config.write_text("project: example\ndeepseek_key_env: CUSTOM_DEEPSEEK\n")
    monkeypatch.setenv("CUSTOM_DEEPSEEK", "secret")
    assert resolve_deepseek_key(load_project_config(config)) == "secret"
    config.write_text("project: example\n")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "default-secret")
    assert resolve_deepseek_key(load_project_config(config)) == "default-secret"


@pytest.mark.parametrize("model", [None, GenerationModel.sonnet, GenerationModel.deepseek])
def test_generation_resolves_only_selected_provider(model, tmp_path, monkeypatch) -> None:
    from tests.test_generate_no_publish import SUCCESS, _engine, _project, _wire

    _project(tmp_path)
    client = _engine(SUCCESS)
    _wire(monkeypatch, tmp_path, client)
    anthropic = MagicMock(return_value="anthropic-secret")
    deepseek = MagicMock(return_value="deepseek-secret")
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(generate_cmd, "resolve_anthropic_key", anthropic)
    monkeypatch.setattr(generate_cmd, "resolve_deepseek_key", deepseek)
    monkeypatch.setattr(generate_cmd, "make_authed_client", factory)
    generate_cmd._run_generate(project_id="p", poll=False, timeout=30, model=model)
    if model == GenerationModel.deepseek:
        anthropic.assert_not_called()
        deepseek.assert_called_once()
        assert factory.call_args.kwargs["anthropic_key"] is None
        assert client.generate.call_args.kwargs["deepseek_key"] == "deepseek-secret"
    else:
        deepseek.assert_not_called()
        anthropic.assert_called_once()
        assert factory.call_args.kwargs["anthropic_key"] == "anthropic-secret"
        assert "deepseek_key" not in client.generate.call_args.kwargs
    if model is None:
        assert "model" not in client.generate.call_args.kwargs
    else:
        assert client.generate.call_args.kwargs["model"] == model


def test_invalid_model_stops_before_project_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer

    load = MagicMock()
    monkeypatch.setattr(generate_cmd, "load_project_config", load)
    with pytest.raises(typer.Exit):
        generate_cmd._run_generate(project_id="p", poll=False, timeout=30, model="typo")
    load.assert_not_called()


def test_project_config_preserves_positional_arguments(tmp_path) -> None:
    from aethis_cli.config import ProjectConfig

    config = ProjectConfig("example", "API_KEY", "ANTHROPIC_KEY", BASE, "project-id", tmp_path)
    assert config.base_url == BASE
    assert config.project_id == "project-id"
    assert config.config_path == tmp_path
    assert config.deepseek_key_env == "DEEPSEEK_API_KEY"


@respx.mock
@pytest.mark.parametrize("thinking", [None, "disabled"])
def test_direct_client_refuses_explicit_thinking_on_old_engine(thinking):
    respx.get(f"{BASE}/openapi.json").respond(
        200, json={"components": {"schemas": {"GenerationModeRequest": {"properties": {"mode": {}}}}}}
    )
    post = respx.post(f"{BASE}/api/v1/public/projects/p/generate").respond(202, json={})
    with AethisClient("ak", BASE) as client:
        with pytest.raises(ValueError, match="no generation was started"):
            client.generate("p", thinking=thinking)
    assert not post.called


def test_warning_text_cannot_break_rich_markup_and_is_shown_once(capsys):
    payload = {"authoring_config": {"warnings": [{"code": "future", "message": "x [/y]"}]}}
    seen = generate_cmd._render_thinking_warnings(payload)
    generate_cmd._render_thinking_warnings(payload, seen)
    assert capsys.readouterr().out.count("x [/y]") == 1


@pytest.mark.parametrize("message", ["escape\x1b[2J", "surrogate\ud800", "bidi\u202e", "newline\nspoof"])
def test_warning_uses_terminal_sanitizer(message, capsys):
    from aethis_cli._terminal_safe import safe_text

    generate_cmd._render_thinking_warnings(
        {"authoring_config": {"warnings": [{"code": "warning", "message": message}]}}
    )
    output = capsys.readouterr().out
    assert safe_text(message) in output
    assert message not in output


@respx.mock
def test_direct_generation_refreshes_cached_control_schema_before_post():
    schema = respx.get(f"{BASE}/openapi.json").mock(
        side_effect=[
            httpx.Response(
                200, json={"components": {"schemas": {"GenerationModeRequest": {"properties": {"thinking": {}}}}}}
            ),
            httpx.Response(
                200, json={"components": {"schemas": {"GenerationModeRequest": {"properties": {"mode": {}}}}}}
            ),
        ]
    )
    post = respx.post(f"{BASE}/api/v1/public/projects/p/generate").respond(202, json={})
    with AethisClient("ak", BASE) as client:
        assert "thinking" in client.generation_mode_request_properties()
        with pytest.raises(ValueError, match="no generation was started"):
            client.generate("p", thinking="disabled")
    assert schema.call_count == 2
    assert not post.called


def test_late_control_rejection_is_rendered_without_traceback(tmp_path, monkeypatch, capsys):
    from tests.test_generate_no_publish import _engine, _project, _wire

    _project(tmp_path)
    client = _engine({})
    client.generation_mode_request_properties.return_value = {"thinking"}
    client.generate.side_effect = ValueError("Control schema could not be read; no generation was started")
    _wire(monkeypatch, tmp_path, client)
    with pytest.raises(__import__("typer").Exit) as rejected:
        generate_cmd._run_generate(project_id="p", poll=False, timeout=30, thinking="disabled")
    assert rejected.value.exit_code == 1
    output = capsys.readouterr().out
    assert "no generation was started" in output
    assert "Traceback" not in output


@respx.mock
def test_unreadable_generation_control_schema_refuses_without_post():
    respx.get(f"{BASE}/openapi.json").respond(500, json={})
    post = respx.post(f"{BASE}/api/v1/public/projects/p/generate").respond(202, json={})
    with AethisClient("ak", BASE) as client:
        with pytest.raises(ValueError, match="schema could not be read"):
            client.generate("p", thinking="disabled")
    assert not post.called
