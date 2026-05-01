"""Tests for `aethis mcp install / uninstall`.

All tests redirect HOME and CWD into a tmp_path so we never touch the
operator's real editor configs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner


def _run(args, env=None):
    from aethis_cli.main import app

    runner = CliRunner()
    return runner.invoke(app, args, env=env or {}, catch_exceptions=False)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch):
    """Isolate HOME, CWD, and credential lookup from the host."""
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)

    # Force the API key resolver onto a deterministic path.
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test_live_xyz")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    return {"home": home, "work": work}


def _cursor_config(home: Path) -> Path:
    return home / ".cursor" / "mcp.json"


def _claude_code_config(work: Path) -> Path:
    return work / ".mcp.json"


def test_install_fresh_creates_file(sandbox):
    """No existing config → new file with just the aethis entry."""
    result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code == 0, result.output

    cfg_path = _cursor_config(sandbox["home"])
    assert cfg_path.exists()

    data = json.loads(cfg_path.read_text())
    assert "mcpServers" in data
    assert "aethis" in data["mcpServers"]
    entry = data["mcpServers"]["aethis"]
    assert entry["command"] == "npx"
    assert entry["args"] == ["-y", "aethis-mcp@latest"]
    assert entry["env"]["AETHIS_API_KEY"] == "ak_test_live_xyz"
    assert entry["env"]["AETHIS_BASE_URL"] == "https://api.aethis.ai"


def test_install_preserves_other_servers(sandbox):
    """Pre-existing servers must survive the install."""
    cfg_path = _cursor_config(sandbox["home"])
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    }
                }
            }
        )
    )

    result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code == 0, result.output

    data = json.loads(cfg_path.read_text())
    assert set(data["mcpServers"].keys()) == {"filesystem", "aethis"}
    assert data["mcpServers"]["filesystem"]["args"][-1] == "/tmp"


def test_install_is_idempotent_and_updates_creds(sandbox, monkeypatch):
    """Re-running install with new creds replaces in place — no duplicates."""
    # First install with one key.
    result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code == 0, result.output

    cfg_path = _cursor_config(sandbox["home"])
    first = json.loads(cfg_path.read_text())
    assert first["mcpServers"]["aethis"]["env"]["AETHIS_API_KEY"] == "ak_test_live_xyz"

    # Rotate the key, install again.
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test_live_rotated")
    result2 = _run(["mcp", "install", "--target", "cursor"])
    assert result2.exit_code == 0, result2.output

    second = json.loads(cfg_path.read_text())
    assert list(second["mcpServers"].keys()) == ["aethis"]  # no duplicates
    assert second["mcpServers"]["aethis"]["env"]["AETHIS_API_KEY"] == "ak_test_live_rotated"


def test_uninstall_removes_only_aethis(sandbox):
    """Uninstall must leave other servers in place."""
    cfg_path = _cursor_config(sandbox["home"])
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    },
                    "aethis": {
                        "command": "npx",
                        "args": ["-y", "aethis-mcp@latest"],
                        "env": {"AETHIS_API_KEY": "ak_old", "AETHIS_BASE_URL": "https://api.aethis.ai"},
                    },
                }
            }
        )
    )

    result = _run(["mcp", "uninstall", "--target", "cursor"])
    assert result.exit_code == 0, result.output

    data = json.loads(cfg_path.read_text())
    assert "aethis" not in data["mcpServers"]
    assert "filesystem" in data["mcpServers"]


def test_install_bad_target_lists_valid_targets(sandbox):
    """Garbage --target value should fail and surface the valid list."""
    result = _run(["mcp", "install", "--target", "atom"])
    assert result.exit_code != 0
    out = result.output
    for valid in ("claude-code", "cursor", "claude-desktop", "windsurf", "all"):
        assert valid in out, f"missing {valid!r} in output: {out}"


def test_install_without_api_key_fails_clearly(tmp_path, monkeypatch):
    """No cached key anywhere → fail with a 'run aethis login' hint."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    # Make keyring lookups deterministic — return nothing.
    from unittest.mock import patch

    with patch("aethis_cli.commands.mcp_cmd.keyring", create=True) as kr:
        kr.get_password.return_value = None
        result = _run(["mcp", "install", "--target", "cursor"])

    assert result.exit_code != 0
    assert "aethis login" in result.output


def test_install_target_all_writes_to_each_client(sandbox):
    """--target all should land an entry in every supported client's path."""
    result = _run(["mcp", "install", "--target", "all"])
    assert result.exit_code == 0, result.output

    home = sandbox["home"]
    work = sandbox["work"]

    # claude-code is project-level
    cc = _claude_code_config(work)
    # cursor lives under HOME
    cu = _cursor_config(home)
    # windsurf lives under HOME/.codeium/windsurf/
    ws = home / ".codeium" / "windsurf" / "mcp_config.json"
    # claude-desktop varies by OS — check both candidates rather than branching here.
    cd_mac = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    cd_lin = home / ".config" / "Claude" / "claude_desktop_config.json"

    assert cc.exists(), "claude-code config missing"
    assert cu.exists(), "cursor config missing"
    assert ws.exists(), "windsurf config missing"
    assert cd_mac.exists() or cd_lin.exists(), "claude-desktop config missing"

    for path in (cc, cu, ws):
        data = json.loads(path.read_text())
        assert "aethis" in data["mcpServers"]


def test_install_claude_code_writes_project_local(sandbox):
    """claude-code config is project-scoped — it must live in CWD, not HOME."""
    result = _run(["mcp", "install", "--target", "claude-code"])
    assert result.exit_code == 0, result.output

    project_cfg = _claude_code_config(sandbox["work"])
    home_cfg = sandbox["home"] / ".mcp.json"

    assert project_cfg.exists()
    assert not home_cfg.exists()


def test_install_respects_base_url_env(sandbox, monkeypatch):
    """If AETHIS_BASE_URL is set, the entry should reflect it."""
    monkeypatch.setenv("AETHIS_BASE_URL", "https://staging.api.aethis.ai")
    result = _run(["mcp", "install", "--target", "cursor"])
    assert result.exit_code == 0, result.output

    cfg_path = _cursor_config(sandbox["home"])
    data = json.loads(cfg_path.read_text())
    assert data["mcpServers"]["aethis"]["env"]["AETHIS_BASE_URL"] == "https://staging.api.aethis.ai"


def test_uninstall_missing_file_is_noop(sandbox):
    """Uninstall when nothing is configured should still exit cleanly."""
    result = _run(["mcp", "uninstall", "--target", "cursor"])
    assert result.exit_code == 0, result.output
    # File should not have been created by uninstall.
    assert not _cursor_config(sandbox["home"]).exists()
