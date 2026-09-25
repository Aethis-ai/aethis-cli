"""Per-generation model and credential boundaries."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import respx
from typer.testing import CliRunner

from aethis_cli.client import AethisClient, GenerationModel
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
