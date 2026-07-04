"""Tests for the shared confirm helper and its non-interactive env bypass."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import typer

from aethis_cli.prompts import confirm_or_abort, is_noninteractive


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch) -> None:
    """Start each test from a genuinely interactive baseline."""
    monkeypatch.delenv("AETHIS_NONINTERACTIVE", raising=False)
    monkeypatch.delenv("CI", raising=False)


def test_assume_yes_skips_prompt_entirely() -> None:
    with patch("aethis_cli.prompts.typer.confirm") as confirm:
        confirm_or_abort("Archive it?", assume_yes=True)
    confirm.assert_not_called()


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "yes"])
def test_noninteractive_env_bypasses_prompt(monkeypatch, capsys, value: str) -> None:
    monkeypatch.setenv("AETHIS_NONINTERACTIVE", value)
    assert is_noninteractive() is True
    with patch("aethis_cli.prompts.typer.confirm") as confirm:
        confirm_or_abort("Archive it?")
    confirm.assert_not_called()
    out = capsys.readouterr().out
    # Fail-loud: the bypass announces itself, naming the env var.
    assert "Non-interactive" in out
    assert "AETHIS_NONINTERACTIVE" in out


def test_ci_env_also_bypasses(monkeypatch) -> None:
    monkeypatch.setenv("CI", "true")
    with patch("aethis_cli.prompts.typer.confirm") as confirm:
        confirm_or_abort("Archive it?")
    confirm.assert_not_called()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_falsey_env_still_prompts(monkeypatch, value: str) -> None:
    monkeypatch.setenv("AETHIS_NONINTERACTIVE", value)
    assert is_noninteractive() is False
    with patch("aethis_cli.prompts.typer.confirm", return_value=True) as confirm:
        confirm_or_abort("Archive it?")
    confirm.assert_called_once()


def test_interactive_decline_raises_abort() -> None:
    with patch("aethis_cli.prompts.typer.confirm", return_value=False):
        with pytest.raises(typer.Abort):
            confirm_or_abort("Archive it?")


def test_interactive_accept_proceeds() -> None:
    with patch("aethis_cli.prompts.typer.confirm", return_value=True):
        confirm_or_abort("Archive it?")  # no exception == proceed
