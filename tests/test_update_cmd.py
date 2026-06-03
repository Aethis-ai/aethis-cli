"""Tests for `aethis update`."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from aethis_cli.commands import update_cmd


def _run(args, **patches_kw):
    from aethis_cli.main import app

    runner = CliRunner()
    defaults = {
        "_fetch_latest_pypi": "9.9.9",
        "_save_cache": None,
        "_is_editable_install": False,
        "_detect_install_method": ("uv", "uv tool upgrade aethis-cli"),
        "subprocess_returncode": 0,
    }
    defaults.update(patches_kw)

    run_result = MagicMock(returncode=defaults["subprocess_returncode"])
    with (
        patch.object(update_cmd, "_fetch_latest_pypi", return_value=defaults["_fetch_latest_pypi"]),
        patch.object(update_cmd, "_save_cache") as save_cache,
        patch.object(update_cmd, "_is_editable_install", return_value=defaults["_is_editable_install"]),
        patch.object(update_cmd, "_detect_install_method", return_value=defaults["_detect_install_method"]),
        patch.object(update_cmd.subprocess, "run", return_value=run_result) as sub_run,
    ):
        result = runner.invoke(app, args)
    return result, sub_run, save_cache


def test_update_already_current() -> None:
    from aethis_cli._version import __version__

    result, sub_run, _ = _run(["update"], _fetch_latest_pypi=__version__)
    assert result.exit_code == 0
    assert "Already up to date" in result.output
    sub_run.assert_not_called()


def test_update_runs_uv_upgrade() -> None:
    result, sub_run, save_cache = _run(["update"])
    assert result.exit_code == 0
    sub_run.assert_called_once_with(["uv", "tool", "upgrade", "aethis-cli"])
    assert "Updated" in result.output
    save_cache.assert_called_once_with("9.9.9")


def test_update_runs_pipx_upgrade() -> None:
    result, sub_run, _ = _run(["update"], _detect_install_method=("pipx", "pipx upgrade aethis-cli"))
    assert result.exit_code == 0
    sub_run.assert_called_once_with(["pipx", "upgrade", "aethis-cli"])


def test_update_check_flag_does_not_install() -> None:
    result, sub_run, _ = _run(["update", "--check"])
    assert result.exit_code == 0
    assert "New release available" in result.output
    sub_run.assert_not_called()


def test_update_pypi_unreachable_errors() -> None:
    result, sub_run, _ = _run(["update"], _fetch_latest_pypi=None)
    assert result.exit_code == 1
    assert "could not reach PyPI" in result.output
    sub_run.assert_not_called()


def test_update_refuses_editable_install() -> None:
    result, sub_run, _ = _run(["update"], _is_editable_install=True)
    assert result.exit_code == 1
    assert "development" in result.output
    sub_run.assert_not_called()


def test_update_propagates_upgrade_failure() -> None:
    result, sub_run, _ = _run(["update"], subprocess_returncode=3)
    assert result.exit_code == 3
    assert "failed" in result.output
    sub_run.assert_called_once()


def test_is_editable_install_reads_direct_url() -> None:
    dist = MagicMock()
    dist.read_text.return_value = json.dumps({"dir_info": {"editable": True}})
    with patch("importlib.metadata.distribution", return_value=dist):
        assert update_cmd._is_editable_install("aethis-cli") is True

    dist.read_text.return_value = json.dumps({"dir_info": {"editable": False}})
    with patch("importlib.metadata.distribution", return_value=dist):
        assert update_cmd._is_editable_install("aethis-cli") is False

    dist.read_text.return_value = None  # regular wheel install: no direct_url.json
    with patch("importlib.metadata.distribution", return_value=dist):
        assert update_cmd._is_editable_install("aethis-cli") is False
