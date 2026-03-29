"""Tests for aethis init command."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from aethis_cli.main import app

runner = CliRunner()


def test_init_creates_project_structure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "my-policy"])
    assert result.exit_code == 0

    proj = tmp_path / "my-policy"
    assert proj.is_dir()
    assert (proj / "aethis.yaml").exists()
    assert (proj / "sources").is_dir()
    assert (proj / "guidance" / "hints.yaml").exists()
    assert (proj / "tests" / "scenarios.yaml").exists()


def test_init_yaml_has_project_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "refund-rules"])
    content = (tmp_path / "refund-rules" / "aethis.yaml").read_text()
    assert "project: refund-rules" in content
    assert "api_key_env:" in content


def test_init_existing_dir_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "existing").mkdir()
    result = runner.invoke(app, ["init", "existing"])
    assert result.exit_code != 0
    assert "already exists" in result.output.lower()


def test_init_gitignore_includes_aethis_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "test-proj"])
    gitignore = (tmp_path / "test-proj" / ".gitignore").read_text()
    assert ".aethis/" in gitignore
