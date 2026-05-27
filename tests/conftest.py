"""Shared fixtures for aethis-cli tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_render_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force table-mode default + reset RenderOpts between tests.

    Two things to defend against:

    1. ``CliRunner.invoke`` swaps stdout for a StringIO buffer, so
       ``sys.stdout.isatty()`` returns False — which would trigger the
       new pipe-friendly JSON autodetect and break every existing test
       that asserts on table output. Force ``_stdout_is_tty`` to True
       so commands behave the way humans see them on a real terminal.
    2. ``--output``, ``--json``, ``--jq`` are set on a module-level
       singleton (``render.RUNTIME``); without a reset, one test's flags
       leak into the next.

    Tests that specifically want to exercise piped-JSON behaviour can
    monkeypatch ``_stdout_is_tty`` back to ``False`` inside the test.
    """
    from aethis_cli import render

    monkeypatch.setattr("aethis_cli.render._stdout_is_tty", lambda: True)
    render.RUNTIME.reset()
    yield
    render.RUNTIME.reset()


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a minimal valid aethis project in a temp dir."""
    (tmp_path / "aethis.yaml").write_text(
        "project: test-policy\napi_key_env: AETHIS_API_KEY\nbase_url: https://test.local\n"
    )
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "policy.md").write_text("# Test Policy\nAll applicants must be over 18.")
    (tmp_path / "guidance").mkdir()
    (tmp_path / "guidance" / "hints.yaml").write_text("hints:\n  - Age must be 18 or over\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "scenarios.yaml").write_text(
        "tests:\n"
        "  - name: eligible\n"
        "    inputs: {age: 25}\n"
        "    expect: {outcome: eligible}\n"
        "  - name: not eligible\n"
        "    inputs: {age: 10}\n"
        "    expect: {outcome: not_eligible}\n"
    )
    return tmp_path
