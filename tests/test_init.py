"""Tests for aethis init command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from aethis_cli.main import app

runner = CliRunner()


# --- helpers ---------------------------------------------------------------


def _patch_auth_present():
    """Pretend an API key is cached so init skips login."""
    return patch(
        "aethis_cli.commands.init_cmd._has_cached_auth",
        return_value=True,
    )


def _patch_auth_missing():
    """Pretend no API key is cached."""
    return patch(
        "aethis_cli.commands.init_cmd._has_cached_auth",
        return_value=False,
    )


# --- positional-arg form (existing behaviour) ------------------------------


def test_init_creates_project_structure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _patch_auth_present():
        result = runner.invoke(app, ["init", "my-policy"])
    assert result.exit_code == 0, result.output

    proj = tmp_path / "my-policy"
    assert proj.is_dir()
    assert (proj / "aethis.yaml").exists()
    assert (proj / "sources").is_dir()
    assert (proj / "guidance" / "hints.yaml").exists()
    assert (proj / "tests" / "scenarios.yaml").exists()


# --- fields home (Part A) --------------------------------------------------


def test_init_ruleset_creates_fields_home(tmp_path, monkeypatch):
    """Fields get a dedicated home, not implied by tests/scenarios.yaml."""
    monkeypatch.chdir(tmp_path)
    with _patch_auth_present():
        result = runner.invoke(app, ["init", "with-fields"])
    assert result.exit_code == 0, result.output
    proj = tmp_path / "with-fields"
    assert (proj / "fields" / "fields.yaml").exists()
    assert "fields:" in (proj / "fields" / "fields.yaml").read_text()


def test_init_default_kind_is_ruleset(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _patch_auth_present():
        runner.invoke(app, ["init", "default-kind"])
    content = (tmp_path / "default-kind" / "aethis.yaml").read_text()
    assert "kind: ruleset" in content


# --- rulebook scaffold (Part B) --------------------------------------------


def test_init_rulebook_scaffold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _patch_auth_present():
        result = runner.invoke(app, ["init", "my-rulebook", "--kind", "rulebook"])
    assert result.exit_code == 0, result.output
    rb = tmp_path / "my-rulebook"
    assert "kind: rulebook" in (rb / "aethis.yaml").read_text()
    assert (rb / "guidance" / "hints.yaml").exists()
    assert (rb / "fields" / "fields.yaml").exists()
    assert (rb / "tests" / "scenarios.yaml").exists()
    assert (rb / "rulesets").is_dir()
    # A rulebook composes rulesets; sources belong to the rulesets, not here.
    assert not (rb / "sources").exists()


def test_init_ruleset_kind_has_sources(tmp_path, monkeypatch):
    """--kind ruleset reproduces today's flat layout (sources present)."""
    monkeypatch.chdir(tmp_path)
    with _patch_auth_present():
        result = runner.invoke(app, ["init", "flat-rs", "--kind", "ruleset"])
    assert result.exit_code == 0, result.output
    proj = tmp_path / "flat-rs"
    assert (proj / "sources").is_dir()
    assert (proj / "fields" / "fields.yaml").exists()


def test_init_rejects_invalid_kind(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _patch_auth_present():
        result = runner.invoke(app, ["init", "bad-kind", "--kind", "widget"])
    assert result.exit_code != 0
    assert "kind" in result.output.lower()


def test_init_yaml_has_project_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _patch_auth_present():
        runner.invoke(app, ["init", "refund-rules"])
    content = (tmp_path / "refund-rules" / "aethis.yaml").read_text()
    assert "project: refund-rules" in content
    assert "api_key_env:" in content


def test_init_existing_dir_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "existing").mkdir()
    with _patch_auth_present():
        result = runner.invoke(app, ["init", "existing"])
    assert result.exit_code != 0
    assert "already exists" in result.output.lower()


def test_init_gitignore_includes_aethis_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _patch_auth_present():
        runner.invoke(app, ["init", "test-proj"])
    gitignore = (tmp_path / "test-proj" / ".gitignore").read_text()
    assert ".aethis/" in gitignore


def test_init_creates_state_dir(tmp_path, monkeypatch):
    """`.aethis/state.json` should exist after init so downstream commands
    that call `write_state()` / `read_state()` don't have to special-case
    a missing directory. project_id is set later by `aethis generate`."""
    import json

    monkeypatch.chdir(tmp_path)
    with _patch_auth_present():
        result = runner.invoke(app, ["init", "stateful-proj"])
    assert result.exit_code == 0, result.output
    state_file = tmp_path / "stateful-proj" / ".aethis" / "state.json"
    assert state_file.exists()
    # Empty placeholder — generate_cmd populates project_id later.
    assert json.loads(state_file.read_text()) == {}


# --- next-step ladder ------------------------------------------------------


def test_init_prints_next_step_ladder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _patch_auth_present():
        result = runner.invoke(app, ["init", "ladder-proj"])
    assert result.exit_code == 0, result.output
    assert "Project initialised: ladder-proj" in result.output
    assert "Next:" in result.output
    assert "aethis generate --poll" in result.output
    assert "aethis test && aethis publish" in result.output


# --- prompted (no-arg) form ------------------------------------------------


def test_init_no_args_prompts_for_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # typer.prompt reads from stdin; CliRunner.invoke supports `input=`.
    with _patch_auth_present():
        result = runner.invoke(app, ["init"], input="prompted-proj\n")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "prompted-proj" / "aethis.yaml").exists()


def test_init_no_args_default_is_cwd_name(tmp_path, monkeypatch):
    workdir = tmp_path / "auto-named"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    # Empty input accepts the default project name (current dir name).
    # The scaffold creates a child directory of that name inside cwd.
    with _patch_auth_present():
        result = runner.invoke(app, ["init"], input="\n")
    assert result.exit_code == 0, result.output
    assert (workdir / "auto-named" / "aethis.yaml").exists()


# --- --no-prompt -----------------------------------------------------------


def test_init_no_prompt_requires_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _patch_auth_present():
        result = runner.invoke(app, ["init", "--no-prompt"])
    assert result.exit_code != 0
    assert "name is required" in result.output.lower()


def test_init_no_prompt_with_name_succeeds(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _patch_auth_present():
        result = runner.invoke(app, ["init", "scripted", "--no-prompt"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "scripted" / "aethis.yaml").exists()


def test_init_no_prompt_skips_login_when_unauthenticated(tmp_path, monkeypatch):
    """--no-prompt must error rather than launch the browser flow."""
    monkeypatch.chdir(tmp_path)
    with (
        _patch_auth_missing(),
        patch("aethis_cli.commands.login_cmd.login") as login_mock,
    ):
        result = runner.invoke(app, ["init", "scripted", "--no-prompt"])
    assert result.exit_code != 0
    assert not login_mock.called, "login must not be invoked under --no-prompt"
    assert "no api key" in result.output.lower()


# --- auth flow -------------------------------------------------------------


def test_init_triggers_login_when_no_auth_cached(tmp_path, monkeypatch):
    """No-auth + interactive mode → init must call the login entry point."""
    monkeypatch.chdir(tmp_path)
    with (
        _patch_auth_missing(),
        patch("aethis_cli.commands.login_cmd.login") as login_mock,
    ):
        result = runner.invoke(app, ["init", "auth-proj"])
    assert result.exit_code == 0, result.output
    assert login_mock.called, "init must call login when no auth is cached"
    # Scaffold should still complete after login returns.
    assert (tmp_path / "auth-proj" / "aethis.yaml").exists()


def test_init_skips_login_when_auth_already_cached(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with (
        _patch_auth_present(),
        patch("aethis_cli.commands.login_cmd.login") as login_mock,
    ):
        result = runner.invoke(app, ["init", "cached-auth-proj"])
    assert result.exit_code == 0, result.output
    assert not login_mock.called


# --- name validation ------------------------------------------------------


def test_init_rejects_invalid_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _patch_auth_present():
        result = runner.invoke(app, ["init", "bad/name"])
    assert result.exit_code != 0
    assert "alphanumeric" in result.output.lower()
