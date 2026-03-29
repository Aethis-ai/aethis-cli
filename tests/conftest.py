"""Shared fixtures for aethis-cli tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a minimal valid aethis project in a temp dir."""
    (tmp_path / "aethis.yaml").write_text(
        "project: test-policy\n"
        "api_key_env: AETHIS_API_KEY\n"
        "base_url: http://test.local\n"
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
