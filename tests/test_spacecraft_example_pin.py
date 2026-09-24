"""Per-PR check that the pinned spacecraft example still resolves.

Runs in normal CI (no credentials, no ``manual`` mark) so a broken pin or a
digest mismatch is caught on the PR that causes it, not by the weekly lane.
"""

from __future__ import annotations

from tests.spacecraft_example import load_fixtures


def test_pinned_fixtures_fetch_and_verify(tmp_path):
    """The pinned Act, scenarios and hints resolve, and the Act is the canonical text."""
    fixtures = load_fixtures(tmp_path)
    assert fixtures["act_path"].stat().st_size > 0
    assert fixtures["cases"]
    assert fixtures["hints"]
