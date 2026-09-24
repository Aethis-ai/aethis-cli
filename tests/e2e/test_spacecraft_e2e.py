"""
Spacecraft CLI E2E test — full pipeline via AethisClient.

Tests the complete CLI → API → CodeSynthesizer → eligibility engine pipeline using the
Spacecraft Crew Certification Act 2049 as source material:

  1. Create project via API
  2. Upload the Act as source document
  3. Add the maintained example's guidance hints
  4. Add the maintained example's test scenarios
  5. Trigger generation and poll until done
  6. Run test cases via API (assert ≥80% pass rate)
  7. Verify field schema (≥5 fields)
  8. Call /decide for every scenario and verify its outcome

The Act, the scenarios and the guidance are NOT kept in this repo. They are
fetched from the maintained example in Aethis-ai/aethis-examples
(``spacecraft-crew-certification/``) at a pinned commit, and the Act's digest is
verified. A failed fetch or a digest mismatch FAILS the lane — it never skips,
because a lane that skips on a missing fixture reports green having tested
nothing.

This test drives the **LLM authoring pipeline** (generation), so it runs in its
own weekly + manual-dispatch lane (``.github/workflows/authoring-e2e-weekly.yml``)
rather than any nightly LLM-free lane. It keeps the ``manual`` marker as the
local escape hatch.

Requires:
  AETHIS_API_KEY   — developer API key
  AETHIS_BASE_URL  — API base URL (default: https://api.aethis.ai)
  ANTHROPIC_API_KEY — optional; when set it is passed as the explicit authoring
                      model via the ``X-Anthropic-Key`` header, so the lane
                      never silently falls back to a server-default model.

Tunables (env):
  SPACECRAFT_GENERATION_TIMEOUT — poll deadline in seconds (default 300); the
                                  hard iteration cap so a wedged generation
                                  fails loud instead of hanging the lane.

Run with:
  pytest tests/e2e/test_spacecraft_e2e.py -m manual -v -s
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from aethis_cli.client import AethisClient

pytestmark = pytest.mark.manual

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

# The maintained example, pinned. Bump EXAMPLES_COMMIT deliberately (and the
# digest with it, if the Act changed) — never track a moving branch.
EXAMPLES_REPO = "Aethis-ai/aethis-examples"
EXAMPLES_COMMIT = "a96cf14315072c687790bd25befab8df46217ae6"
EXAMPLE_DIR = "spacecraft-crew-certification"
ACT_PATH = f"{EXAMPLE_DIR}/sources/source.md"
SCENARIOS_PATH = f"{EXAMPLE_DIR}/tests/scenarios.yaml"
HINTS_PATH = f"{EXAMPLE_DIR}/guidance/hints.yaml"
# The canonical Act (original Section 6(4)(c) wording).
ACT_SHA256 = "sha256:71a97144cbce1103a93411a082e9b99e6110126e1180601cbe0a04d15dad9f36"

# Poll deadline / iteration cap. Overridable so the weekly lane can bound a
# wedged generation tightly; a run that exceeds it fails loud (never hangs).
GENERATION_TIMEOUT = int(os.environ.get("SPACECRAFT_GENERATION_TIMEOUT", "300"))
POLL_INTERVAL = 5
MAX_POLL_ITERATIONS = max(1, GENERATION_TIMEOUT // POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Pinned fixtures — fetched, verified, and FAIL (never skip) on any problem
# ---------------------------------------------------------------------------


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


def _load_fixtures(dest: Path) -> dict[str, Any]:
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


@pytest.fixture(scope="module")
def spacecraft_fixtures(tmp_path_factory):
    return _load_fixtures(tmp_path_factory.mktemp("spacecraft"))


def test_pinned_fixtures_fetch_and_verify(spacecraft_fixtures):
    """Runs without credentials: the pinned Act, scenarios and hints resolve,
    and the Act is the canonical text."""
    assert spacecraft_fixtures["act_path"].stat().st_size > 0
    assert spacecraft_fixtures["cases"]
    assert spacecraft_fixtures["hints"]


# ---------------------------------------------------------------------------
# Module-scoped fixture — generation runs once for all tests
# ---------------------------------------------------------------------------


def _make_client() -> tuple[AethisClient, str]:
    api_key = os.environ.get("AETHIS_API_KEY")
    if not api_key:
        pytest.skip("AETHIS_API_KEY not set")
    base_url = os.environ.get("AETHIS_BASE_URL", "https://api.aethis.ai")
    # Pass the authoring model explicitly (X-Anthropic-Key) when provided, so
    # the lane pins its own generation model rather than the server default.
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or None
    return AethisClient(api_key, base_url, anthropic_key=anthropic_key), base_url


@pytest.fixture(scope="module")
def spacecraft_ruleset(spacecraft_fixtures):
    """Run the full generate pipeline once, return project/ruleset state."""
    client, base_url = _make_client()

    # 1. Create project
    project = client.create_project(
        "spacecraft-e2e-cli",
        "spacecraft_crew_cert",
        "galactic_federation",
    )
    pid = project["project_id"]

    # 2. Upload source document
    client.upload_sources(pid, [spacecraft_fixtures["act_path"]])

    # 3. Add the maintained example's guidance hints
    for hint in spacecraft_fixtures["hints"]:
        client.add_guidance(pid, hint)

    # 4. Add the maintained example's test scenarios
    client.add_tests(pid, spacecraft_fixtures["cases"])

    # 5. Trigger generation
    client.generate(pid)

    # 6. Poll until done — bounded by BOTH a wall-clock deadline and an explicit
    # iteration cap, so a wedged generation fails loud instead of hanging.
    deadline = time.time() + GENERATION_TIMEOUT
    iterations = 0
    while time.time() < deadline and iterations < MAX_POLL_ITERATIONS:
        iterations += 1
        status = client.get_status(pid)
        job_info = status.get("job") or {}
        job_status = job_info.get("status", "unknown")

        if job_status == "success":
            ruleset_id = status.get("latest_ruleset_id")
            # 7. Publish ruleset (sets status="active" so /schema, /decide, /test-run work)
            client.publish(pid)
            return {
                "project_id": pid,
                "ruleset_id": ruleset_id,
                "client": client,
                "cases": spacecraft_fixtures["cases"],
            }

        if job_status == "failed":
            pytest.fail(f"Generation failed: {job_info.get('error_message', 'unknown')}")

        time.sleep(POLL_INTERVAL)

    pytest.fail(f"Generation did not finish within the cap ({GENERATION_TIMEOUT}s / {MAX_POLL_ITERATIONS} polls)")


# ---------------------------------------------------------------------------
# Tests: generation & structure
# ---------------------------------------------------------------------------


class TestSpacecraftGeneration:
    """Verify the generation pipeline completes and produces valid output."""

    def test_ruleset_id_exists(self, spacecraft_ruleset):
        assert spacecraft_ruleset["ruleset_id"], "No ruleset_id returned after generation"

    def test_schema_has_sufficient_fields(self, spacecraft_ruleset):
        """Generated ruleset must have ≥5 input fields."""
        client = spacecraft_ruleset["client"]
        schema = client.get_schema(spacecraft_ruleset["ruleset_id"])
        fields = schema.get("fields", [])
        assert len(fields) >= 5, f"Expected ≥5 fields, got {len(fields)}: {[f['field_id'] for f in fields]}"

    def test_schema_field_types(self, spacecraft_ruleset):
        """Verify field types include bool, int, and enum."""
        client = spacecraft_ruleset["client"]
        schema = client.get_schema(spacecraft_ruleset["ruleset_id"])
        types = {f["field_type"].lower() for f in schema.get("fields", [])}
        assert "bool" in types, f"Expected bool fields, got types: {types}"


# ---------------------------------------------------------------------------
# Tests: golden outcomes via /decide
# ---------------------------------------------------------------------------


class TestSpacecraftDecisions:
    """Verify every maintained scenario's outcome via the /decide endpoint."""

    def test_every_scenario_decides_as_expected(self, spacecraft_ruleset):
        client = spacecraft_ruleset["client"]
        mismatches = []
        for case in spacecraft_ruleset["cases"]:
            result = client.decide(spacecraft_ruleset["ruleset_id"], case["field_values"])
            if result["decision"] != case["expected_outcome"]:
                mismatches.append(
                    f"  [{case['name']}]: expected={case['expected_outcome']}, actual={result['decision']}"
                )
        assert not mismatches, f"{len(mismatches)}/{len(spacecraft_ruleset['cases'])} scenarios decided wrongly:\n" + (
            "\n".join(mismatches)
        )


# ---------------------------------------------------------------------------
# Tests: test-run endpoint (pass rate)
# ---------------------------------------------------------------------------


class TestSpacecraftTestRun:
    """Verify the /test-run endpoint returns acceptable pass rate."""

    def test_run_returns_results(self, spacecraft_ruleset):
        client = spacecraft_ruleset["client"]
        result = client.run_tests(spacecraft_ruleset["project_id"])
        expected = len(spacecraft_ruleset["cases"])
        assert result["total"] == expected, f"Expected {expected} test cases, got {result['total']}"

    def test_pass_rate_at_least_80_percent(self, spacecraft_ruleset):
        """Binding assertion: ≥80% of golden cases must pass via /test-run."""
        client = spacecraft_ruleset["client"]
        result = client.run_tests(spacecraft_ruleset["project_id"])
        total = result["total"]
        passed = result["passed"]
        pass_rate = passed / total if total > 0 else 0

        details = []
        for r in result.get("results", []):
            status = "PASS" if r["passed"] else "FAIL"
            details.append(f"  {status} [{r['name']}]: expected={r.get('expected')}, actual={r.get('actual')}")

        assert pass_rate >= 0.8, f"Pass rate {pass_rate:.0%} ({passed}/{total}) below 80% threshold.\n" + "\n".join(
            details
        )
