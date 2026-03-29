"""Tests for config loading and API key resolution."""

from __future__ import annotations

import pytest
from pathlib import Path

from aethis_cli.config import load_project_config, resolve_api_key
from aethis_cli.errors import ConfigError


def test_load_config_from_cwd(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project)
    cfg = load_project_config()
    assert cfg.project == "test-policy"
    assert cfg.api_key_env == "AETHIS_API_KEY"
    assert cfg.base_url == "http://test.local"


def test_load_config_walks_up(tmp_project, monkeypatch):
    """Should find aethis.yaml when cwd is a subdirectory."""
    subdir = tmp_project / "sources"
    monkeypatch.chdir(subdir)
    cfg = load_project_config()
    assert cfg.project == "test-policy"


def test_load_config_missing_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match="aethis.yaml"):
        load_project_config()


def test_load_config_explicit_path(tmp_project):
    cfg = load_project_config(tmp_project / "aethis.yaml")
    assert cfg.project == "test-policy"


def test_load_config_default_base_url(tmp_path, monkeypatch):
    """When base_url is missing from yaml, use the default."""
    (tmp_path / "aethis.yaml").write_text("project: x\napi_key_env: MY_KEY\n")
    monkeypatch.chdir(tmp_path)
    cfg = load_project_config()
    assert cfg.base_url == "https://api.aethis.ai"


def test_resolve_api_key_from_env(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_live_test123")
    cfg = load_project_config()
    key = resolve_api_key(cfg)
    assert key == "ak_live_test123"


def test_resolve_api_key_from_credentials_file(tmp_project, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_project)
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)

    creds_dir = tmp_path / "config_home" / "aethis"
    creds_dir.mkdir(parents=True)
    (creds_dir / "credentials").write_text("api_key: ak_live_fromfile")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config_home"))

    cfg = load_project_config()
    key = resolve_api_key(cfg)
    assert key == "ak_live_fromfile"


def test_resolve_api_key_missing_raises(tmp_project, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_project)
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty_config"))
    with pytest.raises(ConfigError, match="API key"):
        cfg = load_project_config()
        resolve_api_key(cfg)


def test_config_reads_project_id_from_state(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project)
    state_dir = tmp_project / ".aethis"
    state_dir.mkdir()
    (state_dir / "state.json").write_text('{"project_id": "proj_saved"}')
    cfg = load_project_config()
    assert cfg.project_id == "proj_saved"
