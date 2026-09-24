"""The maintained spacecraft example, fetched at a pinned commit.

Shared by the weekly authoring e2e (``tests/e2e/test_spacecraft_e2e.py``) and the
per-PR pin check (``tests/test_spacecraft_example_pin.py``). Every failure is a
``pytest.fail`` — a missing fixture must never become a skip.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

# The maintained example, pinned. Bump EXAMPLES_COMMIT deliberately (and the
# digest with it, if the Act changed) — never track a moving branch.
EXAMPLES_REPO = "Aethis-ai/aethis-examples"
EXAMPLES_COMMIT = "84cad2956f3c092d64961a107cd224e47f144ae4"
EXAMPLE_DIR = "spacecraft-crew-certification"
ACT_PATH = f"{EXAMPLE_DIR}/sources/source.md"
SCENARIOS_PATH = f"{EXAMPLE_DIR}/tests/scenarios.yaml"
HINTS_PATH = f"{EXAMPLE_DIR}/guidance/hints.yaml"
# The canonical Act (original Section 6(4)(c) wording).
ACT_SHA256 = "sha256:71a97144cbce1103a93411a082e9b99e6110126e1180601cbe0a04d15dad9f36"


def _fetch_pinned(path: str) -> bytes:
    url = f"https://raw.githubusercontent.com/{EXAMPLES_REPO}/{EXAMPLES_COMMIT}/{path}"
    try:
        response = httpx.get(url, timeout=30.0, follow_redirects=True)
    except httpx.HTTPError as e:
        pytest.fail(f"Could not fetch pinned example file {url}: {e}")
    if response.status_code != 200:
        pytest.fail(f"Could not fetch pinned example file {url}: HTTP {response.status_code}")
    if not response.content:
        pytest.fail(f"Pinned example file {url} is empty")
    return response.content


def load_fixtures(dest: Path) -> dict[str, Any]:
    act = _fetch_pinned(ACT_PATH)
    digest = "sha256:" + hashlib.sha256(act).hexdigest()
    if digest != ACT_SHA256:
        pytest.fail(f"Act digest mismatch for {ACT_PATH}@{EXAMPLES_COMMIT}: expected {ACT_SHA256}, got {digest}")
    act_path = dest / "source.md"
    act_path.write_bytes(act)

    scenarios = (yaml.safe_load(_fetch_pinned(SCENARIOS_PATH)) or {}).get("tests") or []
    if not scenarios:
        pytest.fail(f"{SCENARIOS_PATH}@{EXAMPLES_COMMIT} has no scenarios under 'tests'")
    cases = [
        {
            "name": tc["name"],
            "field_values": tc["inputs"],
            "expected_outcome": tc["expect"]["outcome"],
        }
        for tc in scenarios
    ]

    hints = [h for h in (yaml.safe_load(_fetch_pinned(HINTS_PATH)) or {}).get("hints") or [] if h]
    if not hints:
        pytest.fail(f"{HINTS_PATH}@{EXAMPLES_COMMIT} has no entries under 'hints'")
    if not all(isinstance(h, str) for h in hints):
        pytest.fail(f"{HINTS_PATH}@{EXAMPLES_COMMIT} has a non-string hint; this test uploads plain-text hints only")

    return {"act_path": act_path, "cases": cases, "hints": hints}
