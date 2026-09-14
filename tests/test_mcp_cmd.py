"""Tests for safe selected-profile MCP host registration."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner


def _run(args: list[str], env: dict[str, str] | None = None):
    from aethis_cli.main import app

    return CliRunner().invoke(app, args, env=env or {}, catch_exceptions=False)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home, work, xdg = tmp_path / "home", tmp_path / "work", tmp_path / "xdg"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    # ``resolve_cached_key`` imports keyring lazily. Keep tests hermetic even
    # when a developer has an actual Aethis credential in their OS keychain.
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(get_password=lambda *_args: None))
    from aethis_cli.auth_helpers import RUNTIME

    RUNTIME.api_key_override = RUNTIME.base_url_override = RUNTIME.profile_override = None
    yield {"home": home, "work": work, "xdg": xdg}
    # Root command flags are kept in a mutable singleton. Do not let a
    # profile/base-url invocation change unrelated later tests.
    RUNTIME.api_key_override = RUNTIME.base_url_override = RUNTIME.profile_override = None
    os.environ.pop("AETHIS_BASE_URL", None)
    os.environ.pop("AETHIS_API_KEY", None)


def _cursor_config(home: Path) -> Path:
    return home / ".cursor" / "mcp.json"


def _entry(path: Path) -> dict:
    return json.loads(path.read_text())["mcpServers"]["aethis"]


def test_anonymous_install_writes_only_non_secret_references(sandbox):
    result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code == 0, result.output
    entry = _entry(_cursor_config(sandbox["home"]))
    assert entry["command"] == "npx" and entry["args"] == ["-y", "aethis-mcp@latest"]
    assert entry["env"] == {"AETHIS_PROFILE": "anonymous", "XDG_CONFIG_HOME": str(sandbox["xdg"].resolve())}
    serialized = _cursor_config(sandbox["home"]).read_text()
    assert "AETHIS_API_KEY" not in serialized and "AETHIS_BASE_URL" not in serialized


def test_install_preserves_other_servers_and_is_idempotent(sandbox):
    path = _cursor_config(sandbox["home"])
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"filesystem": {"command": "npx", "args": ["-y", "fs"]}}}))
    assert _run(["mcp", "install", "--target", "cursor"]).exit_code == 0
    assert _run(["mcp", "install", "--target", "cursor"]).exit_code == 0
    data = json.loads(path.read_text())
    assert set(data["mcpServers"]) == {"filesystem", "aethis"}


def test_install_migrates_known_legacy_entry_without_repeating_secret(sandbox):
    from aethis_cli.config import set_profile

    set_profile("default", api_key="ak_legacy", base_url="https://api.aethis.ai")
    path = _cursor_config(sandbox["home"])
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "aethis": {
                        "command": "npx",
                        "args": ["-y", "aethis-mcp@latest"],
                        "env": {"AETHIS_API_KEY": "ak_legacy", "AETHIS_BASE_URL": "https://api.aethis.ai"},
                    }
                }
            }
        )
    )
    assert _run(["mcp", "install", "--target", "cursor"]).exit_code == 0
    assert "ak_legacy" not in path.read_text()
    assert _entry(path)["env"]["AETHIS_PROFILE"] == "default"


def test_legacy_host_registration_refuses_without_matching_saved_pair(sandbox):
    path = _cursor_config(sandbox["home"])
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "aethis": {
                        "command": "npx",
                        "args": ["-y", "aethis-mcp@latest"],
                        "env": {"AETHIS_API_KEY": "ak_legacy", "AETHIS_BASE_URL": "https://staging.example"},
                    }
                }
            }
        )
    )
    result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code != 0 and "does not match" in result.output
    assert _entry(path)["env"]["AETHIS_API_KEY"] == "ak_legacy"


def test_missing_named_profile_refuses_before_config_write(sandbox):
    result = _run(["--profile", "typo", "mcp", "install", "--target", "cursor"])
    assert result.exit_code != 0 and "does not exist" in result.output
    assert not _cursor_config(sandbox["home"]).exists()


def test_non_api_key_profile_refuses_before_config_write(sandbox):
    from aethis_cli.config import set_active_profile, set_profile

    set_profile("internal", base_url="https://internal.example", auth_mode="gcloud_id_token")
    set_active_profile("internal")
    result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code != 0 and "unsupported auth_mode" in result.output
    assert not _cursor_config(sandbox["home"]).exists()


def test_empty_xdg_config_home_uses_home_config_directory(sandbox, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code == 0, result.output
    assert _entry(_cursor_config(sandbox["home"]))["env"]["XDG_CONFIG_HOME"] == str(
        (sandbox["home"] / ".config").resolve()
    )


def test_install_preserves_json_host_settings_while_updating_profile(sandbox):
    from aethis_cli.config import set_active_profile, set_profile

    set_profile("first", api_key="ak_first")
    set_profile("second", api_key="ak_second")
    set_active_profile("first")
    assert _run(["mcp", "install", "--target", "cursor"]).exit_code == 0
    path = _cursor_config(sandbox["home"])
    data = json.loads(path.read_text())
    data["mcpServers"]["aethis"].update({"disabled": True, "toolTimeout": 45})
    path.write_text(json.dumps(data))
    set_active_profile("second")

    result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code == 0, result.output
    entry = _entry(path)
    assert entry["env"]["AETHIS_PROFILE"] == "second"
    assert entry["disabled"] is True and entry["toolTimeout"] == 45


def test_legacy_cached_credential_refuses_instead_of_selecting_anonymous(sandbox):
    legacy = sandbox["xdg"] / "aethis" / "credentials.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("api_key: ak_legacy_only\n")

    result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code != 0 and "legacy Aethis credential" in result.output
    assert not _cursor_config(sandbox["home"]).exists()


def test_keyless_default_profile_refuses_legacy_credentials_yaml(sandbox):
    from aethis_cli.config import set_profile

    set_profile("default", base_url="https://staging.example")
    legacy = sandbox["xdg"] / "aethis" / "credentials.yaml"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("api_key: ak_legacy_suffix_only\n")

    result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code != 0 and "legacy Aethis credential" in result.output
    assert not _cursor_config(sandbox["home"]).exists()


def test_keyless_default_profile_refuses_legacy_keychain_value(sandbox):
    from aethis_cli.config import set_profile

    set_profile("default", base_url="https://staging.example")
    with patch("aethis_cli.commands.mcp_cmd.resolve_cached_key", return_value="ak_keychain_suffix_only"):
        result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code != 0 and "legacy Aethis credential" in result.output
    assert not _cursor_config(sandbox["home"]).exists()


def test_install_refuses_ambiguous_existing_entry(sandbox):
    path = _cursor_config(sandbox["home"])
    path.parent.mkdir(parents=True)
    original = {"command": "python", "args": ["-m", "mine"]}
    path.write_text(json.dumps({"mcpServers": {"aethis": original}}))
    result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code != 0 and "refusing to overwrite" in result.output
    assert _entry(path) == original


def test_override_conflict_refuses_before_config_write(sandbox):
    result = _run(["--api-key", "ak_one_off", "mcp", "install", "--target", "cursor"])
    assert result.exit_code != 0 and "does not match" in result.output
    assert not _cursor_config(sandbox["home"]).exists()


def test_selected_profile_is_pinned_and_override_must_match(sandbox):
    from aethis_cli.config import set_active_profile, set_profile

    set_profile("staging", api_key="ak_profile", base_url="https://staging.example")
    set_active_profile("staging")
    result = _run(
        ["--api-key", "ak_profile", "--base-url", "https://staging.example", "mcp", "install", "--target", "cursor"]
    )
    assert result.exit_code == 0, result.output
    entry = _entry(_cursor_config(sandbox["home"]))
    assert entry["env"]["AETHIS_PROFILE"] == "staging"
    assert "ak_profile" not in _cursor_config(sandbox["home"]).read_text()


def test_uninstall_removes_only_known_generated_entry(sandbox):
    assert _run(["mcp", "install", "--target", "cursor"]).exit_code == 0
    path = _cursor_config(sandbox["home"])
    data = json.loads(path.read_text())
    data["mcpServers"]["other"] = {"command": "npx"}
    path.write_text(json.dumps(data))
    result = _run(["mcp", "uninstall", "--target", "cursor"])
    assert result.exit_code == 0, result.output
    assert "aethis" not in json.loads(path.read_text())["mcpServers"]
    assert "other" in json.loads(path.read_text())["mcpServers"]


def test_all_includes_codex_and_uses_safe_references(sandbox):
    registrations: dict[str, dict] = {}

    def fake_codex(argv):
        if argv[:2] == ["get", "aethis"]:
            if "aethis" not in registrations:
                return SimpleNamespace(returncode=1, stdout="", stderr="Error: No MCP server named 'aethis' found.")
            return SimpleNamespace(returncode=0, stdout=json.dumps(registrations["aethis"]))
        if argv[:2] == ["add", "aethis"]:
            registrations["aethis"] = {
                "command": "npx",
                "args": ["-y", "aethis-mcp@latest"],
                "env": {
                    argv[3].split("=", 1)[0]: argv[3].split("=", 1)[1],
                    argv[5].split("=", 1)[0]: argv[5].split("=", 1)[1],
                },
            }
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(argv)

    with (
        patch("aethis_cli.commands.mcp_cmd._codex", side_effect=fake_codex) as command,
        patch("aethis_cli.commands.mcp_cmd.shutil.which", return_value="/usr/bin/codex"),
    ):
        result = _run(["mcp", "install", "--target", "all"])
    assert result.exit_code == 0, result.output
    assert registrations["aethis"]["env"] == {
        "AETHIS_PROFILE": "anonymous",
        "XDG_CONFIG_HOME": str(sandbox["xdg"].resolve()),
    }
    argv_text = " ".join(" ".join(call.args[0]) for call in command.call_args_list)
    assert "AETHIS_API_KEY" not in argv_text and "ak_" not in argv_text
    assert _cursor_config(sandbox["home"]).exists()


def test_all_preflights_missing_codex_before_writing_json_hosts(sandbox):
    with patch("aethis_cli.commands.mcp_cmd.shutil.which", return_value=None):
        result = _run(["mcp", "install", "--target", "all"])
    assert result.exit_code != 0 and "Codex CLI is not installed" in result.output
    assert not _cursor_config(sandbox["home"]).exists()


def test_all_reuses_prepared_codex_snapshot_without_a_second_get(sandbox):
    """A late inspection error cannot follow JSON writes in an all-host install."""
    from typer import BadParameter

    with (
        patch("aethis_cli.commands.mcp_cmd.shutil.which", return_value="/usr/bin/codex"),
        patch("aethis_cli.commands.mcp_cmd._codex_get", side_effect=[None, BadParameter("late get failed")]) as get,
        patch("aethis_cli.commands.mcp_cmd._codex", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")),
    ):
        result = _run(["mcp", "install", "--target", "all"])
    assert result.exit_code == 0, result.output
    assert get.call_count == 1
    assert _cursor_config(sandbox["home"]).exists()


def test_keyless_named_profile_with_legacy_key_refuses_without_fallback(sandbox):
    from aethis_cli.config import set_active_profile, set_profile

    set_profile("staging", base_url="https://staging.example")
    set_active_profile("staging")
    with patch("aethis_cli.commands.mcp_cmd.resolve_cached_key", return_value="ak_legacy_suffix_only"):
        result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code != 0 and "legacy Aethis credential" in result.output
    assert not _cursor_config(sandbox["home"]).exists()


def test_all_non_object_codex_registration_refuses_before_json_writes(sandbox):
    result = SimpleNamespace(returncode=0, stdout="[]", stderr="")
    with (
        patch("aethis_cli.commands.mcp_cmd.shutil.which", return_value="/usr/bin/codex"),
        patch("aethis_cli.commands.mcp_cmd._codex", return_value=result),
    ):
        outcome = _run(["mcp", "install", "--target", "all"])
    assert outcome.exit_code != 0 and "non-object" in outcome.output
    assert not _cursor_config(sandbox["home"]).exists()


def test_all_flat_codex_restrictions_refuse_before_json_writes(sandbox):
    from aethis_cli.config import set_active_profile, set_profile

    set_profile("first", api_key="ak_first")
    set_profile("second", api_key="ak_second")
    set_active_profile("second")
    existing = {
        "command": "npx",
        "args": ["-y", "aethis-mcp@latest"],
        "env": {"AETHIS_PROFILE": "first", "XDG_CONFIG_HOME": str(sandbox["xdg"].resolve())},
        "disabled_tools": ["aethis_publish"],
    }
    with (
        patch("aethis_cli.commands.mcp_cmd.shutil.which", return_value="/usr/bin/codex"),
        patch("aethis_cli.commands.mcp_cmd._codex_get", return_value=existing) as get,
    ):
        result = _run(["mcp", "install", "--target", "all"])
    assert result.exit_code != 0 and "cannot preserve" in result.output
    assert get.call_count == 1
    assert not _cursor_config(sandbox["home"]).exists()


def test_all_uninstall_preflights_missing_codex_before_removing_json_hosts(sandbox):
    from aethis_cli.commands.mcp_cmd import _config_path_for

    registration = {
        "mcpServers": {
            "aethis": {
                "command": "npx",
                "args": ["-y", "aethis-mcp@latest"],
                "env": {"AETHIS_PROFILE": "anonymous", "XDG_CONFIG_HOME": str(sandbox["xdg"].resolve())},
            }
        }
    }
    paths = [_config_path_for(target) for target in ("claude-code", "cursor", "claude-desktop", "windsurf")]
    before: dict[Path, str] = {}
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(registration))
        before[path] = path.read_text()

    with patch("aethis_cli.commands.mcp_cmd.shutil.which", return_value=None):
        result = _run(["mcp", "uninstall", "--target", "all"])
    assert result.exit_code != 0 and "Codex CLI is not installed" in result.output
    assert {path: path.read_text() for path in paths} == before


def test_codex_get_surfaces_non_not_found_errors():
    from typer import BadParameter

    result = SimpleNamespace(returncode=2, stdout="", stderr="permission denied")
    with patch("aethis_cli.commands.mcp_cmd._codex", return_value=result), pytest.raises(BadParameter):
        from aethis_cli.commands.mcp_cmd import _codex_get

        _codex_get()


def test_codex_add_failure_after_remove_names_lost_prior_registration(sandbox):
    from aethis_cli.config import set_active_profile, set_profile

    set_profile("first", api_key="ak_first")
    set_profile("second", api_key="ak_second")
    set_active_profile("second")
    existing = {
        "name": "aethis",
        "enabled": True,
        "disabled_reason": None,
        "transport": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "aethis-mcp@latest"],
            "env": {"AETHIS_PROFILE": "first", "XDG_CONFIG_HOME": str(sandbox["xdg"].resolve())},
            "env_vars": [],
            "cwd": None,
        },
        "enabled_tools": None,
        "disabled_tools": None,
        "startup_timeout_sec": None,
        "tool_timeout_sec": None,
    }

    def fake_codex(argv):
        if argv[:2] == ["get", "aethis"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(existing), stderr="")
        if argv[:2] == ["remove", "aethis"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:2] == ["add", "aethis"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="failure")
        raise AssertionError(argv)

    with (
        patch("aethis_cli.commands.mcp_cmd._codex", side_effect=fake_codex),
        patch("aethis_cli.commands.mcp_cmd.shutil.which", return_value="/usr/bin/codex"),
    ):
        result = _run(["mcp", "install", "--target", "codex"])
    assert result.exit_code != 0 and "removed the prior" in result.output


def test_codex_legacy_registration_refuses_without_matching_saved_pair(sandbox):
    from aethis_cli.config import set_active_profile, set_profile

    set_profile("selected", api_key="ak_selected")
    set_active_profile("selected")
    existing = {
        "name": "aethis",
        "transport": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "aethis-mcp@latest"],
            "env": {"AETHIS_API_KEY": "ak_old", "AETHIS_BASE_URL": "https://api.aethis.ai"},
            "env_vars": [],
            "cwd": None,
        },
    }
    calls: list[list[str]] = []

    def fake_codex(argv):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(existing), stderr="")

    with (
        patch("aethis_cli.commands.mcp_cmd._codex", side_effect=fake_codex),
        patch("aethis_cli.commands.mcp_cmd.shutil.which", return_value="/usr/bin/codex"),
    ):
        result = _run(["mcp", "install", "--target", "codex"])
    assert result.exit_code != 0 and "does not match" in result.output
    assert calls == [["get", "aethis", "--json"]]


def test_codex_existing_user_registration_is_not_replaced(sandbox):
    with (
        patch("aethis_cli.commands.mcp_cmd._codex_get", return_value={"command": "python", "args": ["-m", "custom"]}),
        patch("aethis_cli.commands.mcp_cmd.shutil.which", return_value="/usr/bin/codex"),
    ):
        result = _run(["mcp", "install", "--target", "codex"])
    assert result.exit_code != 0 and "refusing to overwrite" in result.output


def test_codex_profile_change_refuses_to_drop_host_restrictions(sandbox):
    from aethis_cli.config import set_active_profile, set_profile

    set_profile("first", api_key="ak_first")
    set_profile("second", api_key="ak_second")
    set_active_profile("second")
    envelope = {
        "name": "aethis",
        "enabled": False,
        "disabled_reason": "disabled by user",
        "transport": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "aethis-mcp@latest"],
            "env": {"AETHIS_PROFILE": "first", "XDG_CONFIG_HOME": str(sandbox["xdg"].resolve())},
            "env_vars": [],
            "cwd": None,
        },
        "enabled_tools": None,
        "disabled_tools": ["aethis_publish"],
        "startup_timeout_sec": 15,
        "tool_timeout_sec": 30,
    }
    calls: list[list[str]] = []

    def fake_codex(argv):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(envelope))

    with (
        patch("aethis_cli.commands.mcp_cmd._codex", side_effect=fake_codex),
        patch("aethis_cli.commands.mcp_cmd.shutil.which", return_value="/usr/bin/codex"),
    ):
        result = _run(["mcp", "install", "--target", "codex"])
    assert result.exit_code != 0 and "cannot preserve" in result.output
    assert calls == [["get", "aethis", "--json"]]
