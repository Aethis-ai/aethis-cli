"""Tests for `aethis status` — CLI context + optional generation progress."""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    """Strip ANSI escape sequences so substring checks survive Rich's auto-highlighting."""
    return _ANSI_RE.sub("", s)


def _run_status(args=None, env=None):
    from aethis_cli.main import app

    runner = CliRunner()
    return runner.invoke(
        app,
        ["status"] + (args or []),
        env=env or {},
        catch_exceptions=False,
    )


def test_status_no_config_no_key_shows_defaults(tmp_path, monkeypatch):
    """From a dir with no aethis.yaml and no API key, status still runs
    and reports: default server, no project, no identity — never errors."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    # Neutralise keyring + credentials fallback
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty_xdg"))

    result = _run_status()

    assert result.exit_code == 0, result.output
    assert "aethis v" in result.output
    assert "api.aethis.ai" in result.output
    assert "no aethis.yaml" in result.output
    assert "no API key" in result.output


def test_status_with_base_url_env_shows_override(tmp_path, monkeypatch):
    """AETHIS_BASE_URL=http://localhost:8080 should flow into the Server line
    and be labelled as coming from the env var."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_BASE_URL", "http://localhost:8080")
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty_xdg"))

    result = _run_status()

    assert result.exit_code == 0, result.output
    assert "http://localhost:8080" in result.output
    assert "AETHIS_BASE_URL" in result.output


def test_status_with_api_key_shows_identity(tmp_path, monkeypatch):
    """With a key set, status should hit /me and show key_id + tenant + tier."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)

    fake_me = {
        "key_id": "ak_abc123",
        "tenant_id": "tenant_x",
        "rate_limit_tier": "pro",
        "scopes": ["decide", "rulesets:read"],
        "can_author": False,
    }

    with patch("aethis_cli.client.AethisClient.whoami", return_value=fake_me):
        result = _run_status()

    assert result.exit_code == 0, result.output
    assert "ak_abc123" in result.output
    assert "tenant_x" in result.output
    assert "pro" in result.output
    assert "read-only" in result.output


def test_status_with_yaml_shows_project_context(tmp_project, monkeypatch):
    """From inside a project dir, status should show the project + config path."""
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)

    with patch("aethis_cli.client.AethisClient.whoami", side_effect=Exception("skip")):
        # Let identity section fail gracefully; we only care about the project section.
        pass
    with patch(
        "aethis_cli.client.AethisClient.whoami",
        return_value={"key_id": "ak_x", "tenant_id": "t", "rate_limit_tier": "free", "scopes": [], "can_author": False},
    ):
        result = _run_status()

    assert result.exit_code == 0, result.output
    assert "test-policy" in result.output
    assert "aethis.yaml" in result.output
    assert "test.local" in result.output
    assert "from aethis.yaml" in result.output


def _write_multi_profile_credentials(tmp_path: Path, profile: str, api_key: str) -> Path:
    """Write the multi-profile credentials YAML the way `aethis login` does."""
    creds_dir = tmp_path / "aethis"
    creds_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    creds = creds_dir / "credentials"
    payload = {"active_profile": profile, "profiles": {profile: {"api_key": api_key}}}
    fd = os.open(str(creds), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(yaml.dump(payload))
    return creds


def test_status_reads_multi_profile_credentials_file(tmp_path, monkeypatch):
    """Regression: `aethis login` writes profiles.<name>.api_key but status used
    to look for a flat top-level `api_key` and reported "no API key" while
    other commands worked fine.

    Trip-wire so the asymmetry doesn't reappear.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_multi_profile_credentials(tmp_path, "default", "ak_live_regression_canary")

    fake_me = {
        "key_id": "ak_live_regression_canary",
        "tenant_id": "tenant_x",
        "rate_limit_tier": "internal",
        "scopes": ["decide", "rulesets:write"],
        "can_author": True,
    }
    with patch("aethis_cli.client.AethisClient.whoami", return_value=fake_me):
        result = _run_status()

    assert result.exit_code == 0, result.output
    assert "ak_live_regression_canary" in result.output, (
        "status read the credentials file but didn't surface the resolved key — "
        "check the call site to resolve_cached_key()"
    )
    assert "no API key" not in result.output


def test_whoami_reads_multi_profile_credentials_file(tmp_path, monkeypatch):
    """Regression mirror of the status test above, for `aethis whoami`."""
    from aethis_cli.main import app

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_multi_profile_credentials(tmp_path, "default", "ak_live_whoami_canary")

    fake_me = {
        "key_id": "ak_live_whoami_canary",
        "tenant_id": "tenant_x",
        "rate_limit_tier": "internal",
        "scopes": ["decide", "rulesets:write"],
        "can_author": True,
    }
    runner = CliRunner()
    with patch("aethis_cli.client.AethisClient.whoami", return_value=fake_me):
        result = runner.invoke(app, ["whoami"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "ak_live_whoami_canary" in result.output
    assert "No Aethis API key" not in result.output


def test_status_with_project_id_shows_generation_progress(tmp_project, monkeypatch):
    """--project-id adds a generation progress section after the global summary."""
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)

    fake_me = {
        "key_id": "ak_x",
        "tenant_id": "t",
        "rate_limit_tier": "free",
        "scopes": [],
        "can_author": False,
    }
    fake_gen = {
        "project_status": "ready",
        "job": {"status": "completed", "progress_percent": 100},
        "latest_ruleset_id": "test:20260419-abc1234",
    }

    with (
        patch("aethis_cli.client.AethisClient.whoami", return_value=fake_me),
        patch("aethis_cli.client.AethisClient.get_status", return_value=fake_gen),
    ):
        result = _run_status(args=["-p", "proj_abc"])

    assert result.exit_code == 0, result.output
    clean = _strip_ansi(result.output)
    assert "Generation" in clean
    assert "proj_abc" in clean
    assert "ready" in clean
    assert "test:20260419-abc1234" in clean
