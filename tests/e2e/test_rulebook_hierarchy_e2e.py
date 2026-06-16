"""
Rulebook → ruleset hierarchy CLI E2E test.

Drives the field-authoring loop end-to-end through the real `aethis` CLI against
a live Aethis API, for the `examples/community-grants-rulebook` example.

The example is a rulebook with one SHARED field (`applicant.is_borough_resident`)
and two member rulesets whose own `fields/fields.yaml` start empty. The headline
assertion: after generating each member, the shared rulebook field appears in the
member's produced schema — even though the member never declared it locally. That
is the proof that a field defined once at the rulebook level propagates down into
every member ruleset (rulebook wins).

Kept deliberately lightweight: it checks field propagation + the new commands
round-tripping, not /decide outcome correctness. (Strict enforcement that a
produced ruleset uses the *pinned* rulebook key is a later phase, aethis-core#194;
the deterministic merge this CLI performs is unit-tested separately in
tests/test_generate_fields.py.)

Requires:
  AETHIS_API_KEY   — any value works against a DISABLE_AUTH backend
  AETHIS_BASE_URL  — API base URL (a local aethis-core on :8080 is ideal; it is
                     disposable and uses its own server-side LLM key, so no
                     ANTHROPIC_API_KEY is needed in this process)

Run with:
  AETHIS_BASE_URL=http://localhost:8080 AETHIS_API_KEY=test \
    uv run pytest tests/e2e/test_rulebook_hierarchy_e2e.py -m manual -v -s
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.manual

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIR = REPO_ROOT / "examples" / "community-grants-rulebook"
MEMBER_RULESETS = ["youth-activity-grant", "senior-wellbeing-grant"]
SHARED_FIELD = "applicant.is_borough_resident"
CLI_TIMEOUT = 360  # seconds per CLI call (generation is the slow one)


def _require_live_backend() -> None:
    if not os.environ.get("AETHIS_API_KEY"):
        pytest.skip("AETHIS_API_KEY not set")
    if not os.environ.get("AETHIS_BASE_URL"):
        pytest.skip("AETHIS_BASE_URL not set — point at a disposable backend (e.g. local aethis-core)")
    if not EXAMPLE_DIR.exists():
        pytest.skip(f"Example not found: {EXAMPLE_DIR}")


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Invoke the real CLI (`python -m aethis_cli`) in `cwd` and echo its output."""
    env = dict(os.environ)
    env.setdefault("AETHIS_API_KEY", "test")
    env.setdefault("TERM", "dumb")
    env.setdefault("NO_COLOR", "1")
    # Pin the worktree package so `-m aethis_cli` can't resolve to a globally
    # installed older aethis-cli when run from a temp working directory.
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run(
        [sys.executable, "-m", "aethis_cli", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT,
    )
    sys.stdout.write(f"\n$ aethis {' '.join(args)}  (cwd={cwd.name})\n{result.stdout}{result.stderr}")
    return result


def _fields_in(fields_yaml: Path) -> set[str]:
    raw = yaml.safe_load(fields_yaml.read_text()) or {}
    return {f["key"] for f in (raw.get("fields") or []) if isinstance(f, dict) and f.get("key")}


@pytest.fixture(scope="module")
def authored_rulebook(tmp_path_factory):
    """Copy the example to a scratch dir and author both rulesets.

    Runs validate → generate → pull per member. Discovery is exercised
    separately (test_discover_populates_vocabulary) so it can't pollute the
    member's own vocabulary here — keeping the propagation assertion clean:
    the shared field can only arrive via the rulebook.
    """
    _require_live_backend()

    workdir = tmp_path_factory.mktemp("community-grants") / "community-grants-rulebook"
    shutil.copytree(EXAMPLE_DIR, workdir)

    for name in MEMBER_RULESETS:
        rs_dir = workdir / "rulesets" / name

        # Precondition: the member's own vocabulary is empty — so anything that
        # shows up after generation came from the rulebook, not from here.
        assert _fields_in(rs_dir / "fields" / "fields.yaml") == set()

        val = _run_cli(["fields", "validate"], rs_dir)
        assert val.returncode == 0, f"{name}: fields validate failed"

        gen = _run_cli(["generate", "--poll", "--timeout", str(CLI_TIMEOUT - 30)], rs_dir)
        assert gen.returncode == 0, f"{name}: generate failed"

        pull = _run_cli(["fields", "pull"], rs_dir)
        assert pull.returncode == 0, f"{name}: fields pull failed"

    return workdir


def test_each_ruleset_generated_a_ruleset(authored_rulebook):
    """Every member ruleset produced a ruleset id (generation succeeded)."""
    for name in MEMBER_RULESETS:
        state = json.loads((authored_rulebook / "rulesets" / name / ".aethis" / "state.json").read_text())
        assert state.get("ruleset_id"), f"{name}: no ruleset_id in state.json"


@pytest.mark.parametrize("name", MEMBER_RULESETS)
def test_shared_rulebook_field_propagated(authored_rulebook, name):
    """The shared rulebook field appears in the member ruleset after generation,
    even though the member's own fields.yaml never declared it."""
    pulled = _fields_in(authored_rulebook / "rulesets" / name / "fields" / "fields.yaml")
    assert SHARED_FIELD in pulled, (
        f"{name}: shared rulebook field {SHARED_FIELD!r} not propagated — got {sorted(pulled)}"
    )


def test_discover_populates_vocabulary(tmp_path):
    """`aethis fields discover` seeds the member's fields.yaml from its sources,
    and the result passes `aethis fields validate`."""
    _require_live_backend()
    rs_dir = tmp_path / "community-grants-rulebook" / "rulesets" / MEMBER_RULESETS[0]
    shutil.copytree(EXAMPLE_DIR, tmp_path / "community-grants-rulebook")

    assert _fields_in(rs_dir / "fields" / "fields.yaml") == set()

    disc = _run_cli(["fields", "discover"], rs_dir)
    assert disc.returncode == 0, "fields discover failed"
    assert _fields_in(rs_dir / "fields" / "fields.yaml"), "discover wrote no fields"

    val = _run_cli(["fields", "validate"], rs_dir)
    assert val.returncode == 0, "fields validate failed after discover"
