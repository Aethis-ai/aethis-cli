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
        "_detect_install_method": "uv",
        "_fetch_github_releases": [],
        "subprocess_returncode": 0,
    }
    defaults.update(patches_kw)

    run_result = MagicMock(returncode=defaults["subprocess_returncode"])
    with (
        patch.object(update_cmd, "_fetch_latest_pypi", return_value=defaults["_fetch_latest_pypi"]),
        patch.object(update_cmd, "_save_cache") as save_cache,
        patch.object(update_cmd, "_is_editable_install", return_value=defaults["_is_editable_install"]),
        patch.object(update_cmd, "_detect_install_method", return_value=defaults["_detect_install_method"]),
        patch.object(update_cmd, "_fetch_github_releases", return_value=defaults["_fetch_github_releases"]),
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
    result, sub_run, _ = _run(["update"], _detect_install_method="pipx")
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


def test_update_check_renders_releases_in_range() -> None:
    """(a) a normal 2-version mocked range renders both titles + notes."""
    releases = [
        ("v9.9.9", "9.9.9", "Added widgets."),
        ("v9.9.8", "9.9.8", "Fixed bugs."),
        ("v0.1.0", "0.1.0", "Ancient, out of range."),
    ]
    result, _, _ = _run(
        ["update", "--check"],
        _fetch_latest_pypi="9.9.9",
        _fetch_github_releases=releases,
    )
    assert result.exit_code == 0
    assert "9.9.9" in result.output
    assert "Added widgets." in result.output
    assert "9.9.8" in result.output
    assert "Fixed bugs." in result.output
    assert "Ancient, out of range." not in result.output


def test_update_check_falls_back_on_empty_releases() -> None:
    """(b) an empty Releases result falls back to a link, never raises."""
    result, _, _ = _run(["update", "--check"], _fetch_github_releases=[])
    assert result.exit_code == 0
    assert update_cmd._RELEASES_PAGE_URL in result.output


def test_update_check_falls_back_when_releases_fetch_raises() -> None:
    """(b) the Releases call itself erroring falls back without raising."""
    from aethis_cli.main import app

    runner = CliRunner()
    with (
        patch.object(update_cmd, "_fetch_latest_pypi", return_value="9.9.9"),
        patch.object(update_cmd, "_save_cache"),
        patch.object(update_cmd, "_fetch_github_releases", side_effect=RuntimeError("boom")),
    ):
        result = runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 0
    assert update_cmd._RELEASES_PAGE_URL in result.output


def test_releases_in_range_excludes_current_and_below() -> None:
    """(c) the range (current, latest] excludes current and anything <= current."""
    releases = [
        ("v9.9.9", "9.9.9", "in range"),
        ("v9.9.0", "9.9.0", "current, excluded"),
        ("v9.0.0", "9.0.0", "below current, excluded"),
        ("v10.0.0", "10.0.0", "above latest, excluded"),
    ]
    selected = update_cmd._releases_in_range(releases, current="9.9.0", latest="9.9.9")
    assert selected == [("v9.9.9", "9.9.9", "in range")]


def test_releases_in_range_sparse_gap_renders_what_exists() -> None:
    """(e) a gapped Releases list (some in-range versions have no Release) renders
    what exists and never crashes — mirrors the real forward-fill launch state."""
    releases = [
        ("v9.9.9", "9.9.9", "latest notes"),
        # no Release for 9.9.8 or 9.9.7 — the gap.
        ("v9.9.6", "9.9.6", "older notes"),
    ]
    selected = update_cmd._releases_in_range(releases, current="9.9.5", latest="9.9.9")
    assert selected == [
        ("v9.9.9", "9.9.9", "latest notes"),
        ("v9.9.6", "9.9.6", "older notes"),
    ]


def test_update_check_sparse_releases_render_without_crashing() -> None:
    """(e) end-to-end: a gapped range still prints via the update command."""
    releases = [
        ("v9.9.9", "9.9.9", "latest notes"),
        ("v9.9.6", "9.9.6", "older notes"),
    ]
    result, _, _ = _run(
        ["update", "--check"],
        _fetch_latest_pypi="9.9.9",
        _fetch_github_releases=releases,
    )
    assert result.exit_code == 0
    assert "latest notes" in result.output
    assert "older notes" in result.output


def test_releases_in_range_caps_and_truncates() -> None:
    """Newest ≤5 versions shown; long notes truncated with an ellipsis."""
    releases = [(f"v9.9.{i}", f"9.9.{i}", "x" * 1000) for i in range(9, -1, -1)]  # v9.9.9 .. v9.9.0
    selected = update_cmd._releases_in_range(releases, current="9.8.0", latest="9.9.9")
    assert len(selected) == update_cmd._CHANGELOG_MAX_ENTRIES
    assert [tag for tag, _, _ in selected] == ["v9.9.9", "v9.9.8", "v9.9.7", "v9.9.6", "v9.9.5"]

    result, _, _ = _run(
        ["update", "--check"],
        _fetch_latest_pypi="9.9.9",
        _fetch_github_releases=releases,
    )
    assert result.exit_code == 0
    assert "…" in result.output


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
