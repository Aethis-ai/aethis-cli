"""
Spacecraft CLI E2E test — full pipeline via AethisClient.

Tests the complete CLI → API → CodeSynthesizer → eligibility engine pipeline using the
Spacecraft Crew Certification Act 2049 as source material:

  1. Create project via API
  2. Upload the Act as source document
  3. Add prescriptive guidance hints
  4. Add golden test cases
  5. Trigger generation and poll until done
  6. Run test cases via API (assert ≥80% pass rate)
  7. Verify field schema (≥5 fields)
  8. Call /decide with known inputs and verify outcomes

Requires:
  AETHIS_API_KEY  — developer API key
  AETHIS_BASE_URL — API base URL (default: https://api.aethis.ai)

Run with:
  pytest tests/e2e/test_spacecraft_e2e.py -m manual -v -s
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from aethis_cli.client import AethisClient

pytestmark = pytest.mark.manual

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

SPACECRAFT_POLICY_PATH = (
    Path(__file__).resolve().parents[3]
    / "tda-server"
    / "docs"
    / "test-fixtures"
    / "spacecraft-crew-certification-act.md"
)

GENERATION_TIMEOUT = 300  # 5 minutes
POLL_INTERVAL = 5

# ---------------------------------------------------------------------------
# Guidance hints — prescriptive DSL structure (same as tda-server e2e)
# ---------------------------------------------------------------------------

GUIDANCE_HINTS = [
    (
        "Use these EXACT field keys from Appendix A of the Act: "
        "space.crew.species (Sort.ENUM: Human, Vogon, Magrathean, Betelgeusian, Dolphin), "
        "space.crew.age (Sort.INT), "
        "space.crew.flight_hours (Sort.INT), "
        "space.crew.has_pilot_license (Sort.BOOL), "
        "space.crew.has_gaa_exam (Sort.BOOL), "
        "space.crew.has_approved_provider_cert (Sort.BOOL), "
        "space.crew.has_radiation_cert (Sort.BOOL), "
        "space.crew.has_towel (Sort.BOOL), "
        "space.medical.cert_valid (Sort.BOOL), "
        "space.mission.type (Sort.ENUM: orbital, suborbital, lunar), "
        "space.vessel.propulsion_type (Sort.ENUM: ion, fusion, bistromatic, conventional)"
    ),
    "Generate FieldDefinition and Criterion objects. Use ONLY the group names listed below — do NOT invent additional groups.",
    (
        "Vogon species disqualified (group='species_check'): "
        "Op(NE, [FieldRef(key='space.crew.species'), Const(sort=Sort.ENUM, value='Vogon')])"
    ),
    (
        "Flight readiness (group='flight_readiness'): SINGLE Criterion using "
        "Op(OR, [Op(GE, [FieldRef(key='space.crew.age'), Const(sort=Sort.INT, value=60)]), "
        "Op(AND, [Op(GE, [FieldRef(key='space.crew.flight_hours'), Const(sort=Sort.INT, value=500)]), "
        "Op(EQ, [FieldRef(key='space.crew.has_pilot_license'), Const(sort=Sort.BOOL, value=True)])])]). "
        "Age >= 60 is an exemption alternative, NOT a separate group."
    ),
    (
        "Medical certification (group='medical_certification'): two Criterion objects "
        "in the SAME group (OR'd): route A = has_gaa_exam EQ True, "
        "route B = has_approved_provider_cert EQ True"
    ),
    "Medical cert validity (group='medical_cert_validity'): space.medical.cert_valid EQ True",
    (
        "Radiation for orbital missions (group='radiation_cert'): SINGLE Criterion using "
        "Op(IMPLIES, [Op(EQ, [FieldRef(key='space.mission.type'), Const(sort=Sort.ENUM, value='orbital')]), "
        "Op(EQ, [FieldRef(key='space.crew.has_radiation_cert'), Const(sort=Sort.BOOL, value=True)])]). "
        "Non-orbital missions do NOT need radiation cert."
    ),
    "Towel required (group='towel_compliance'): space.crew.has_towel EQ True",
    (
        "IMPORTANT: Do NOT create separate groups or criteria for Section 6 exception "
        "levels (A/B/C). The three-level exception chain (age exempt, orbital override, "
        "veteran override) is already simplified into the flight_readiness group as an "
        "OR alternative. Creating separate 'exception_level_a/b/c' groups would make "
        "them mandatory for ALL applicants, breaking eligibility. Only generate criteria "
        "for the groups explicitly listed above: species_check, flight_readiness, "
        "medical_certification, medical_cert_validity, radiation_cert, towel_compliance."
    ),
]

# ---------------------------------------------------------------------------
# Golden test cases — from the Spacecraft Crew Certification Act 2049
# ---------------------------------------------------------------------------

GOLDEN_CASES: list[tuple[str, dict[str, Any], str]] = [
    (
        "Vogon crew member — disqualifying species (Section 3)",
        {"space.crew.species": "Vogon"},
        "not_eligible",
    ),
    (
        "No towel — mandatory equipment violation (Section 9)",
        {
            "space.crew.species": "Human",
            "space.crew.flight_hours": 600,
            "space.crew.has_pilot_license": True,
            "space.crew.has_gaa_exam": True,
            "space.medical.cert_valid": True,
            "space.crew.age": 35,
            "space.mission.type": "suborbital",
            "space.crew.has_towel": False,
        },
        "not_eligible",
    ),
    (
        "Orbital mission — radiation cert absent (Section 5)",
        {
            "space.crew.species": "Human",
            "space.crew.flight_hours": 600,
            "space.crew.has_pilot_license": True,
            "space.crew.has_gaa_exam": True,
            "space.medical.cert_valid": True,
            "space.crew.age": 35,
            "space.mission.type": "orbital",
            "space.crew.has_radiation_cert": False,
            "space.crew.has_towel": True,
        },
        "not_eligible",
    ),
    (
        "Full compliance — Human, suborbital, all requirements met",
        {
            "space.crew.species": "Human",
            "space.crew.flight_hours": 600,
            "space.crew.has_pilot_license": True,
            "space.crew.has_gaa_exam": True,
            "space.crew.has_approved_provider_cert": True,
            "space.crew.has_radiation_cert": True,
            "space.medical.cert_valid": True,
            "space.crew.age": 35,
            "space.mission.type": "suborbital",
            "space.vessel.propulsion_type": "conventional",
            "space.crew.has_towel": True,
        },
        "eligible",
    ),
    (
        "Age exemption — senior crew (age >= 60) exempt from flight readiness (Section 6)",
        {
            "space.crew.species": "Human",
            "space.crew.age": 65,
            "space.crew.has_gaa_exam": True,
            "space.crew.has_approved_provider_cert": True,
            "space.crew.has_radiation_cert": True,
            "space.medical.cert_valid": True,
            "space.mission.type": "suborbital",
            "space.vessel.propulsion_type": "conventional",
            "space.crew.has_towel": True,
        },
        "eligible",
    ),
]


# ---------------------------------------------------------------------------
# Module-scoped fixture — generation runs once for all tests
# ---------------------------------------------------------------------------


def _make_client() -> tuple[AethisClient, str]:
    api_key = os.environ.get("AETHIS_API_KEY")
    if not api_key:
        pytest.skip("AETHIS_API_KEY not set")
    base_url = os.environ.get("AETHIS_BASE_URL", "https://api.aethis.ai")
    return AethisClient(api_key, base_url), base_url


@pytest.fixture(scope="module")
def spacecraft_bundle():
    """Run the full generate pipeline once, return project/bundle state."""
    client, base_url = _make_client()

    if not SPACECRAFT_POLICY_PATH.exists():
        pytest.skip(f"Source doc not found: {SPACECRAFT_POLICY_PATH}")

    # 1. Create project
    project = client.create_project(
        "spacecraft-e2e-cli", "spacecraft_crew_cert", "galactic_federation",
    )
    pid = project["project_id"]

    # 2. Upload source document
    client.upload_sources(pid, [SPACECRAFT_POLICY_PATH])

    # 3. Add guidance hints
    for hint in GUIDANCE_HINTS:
        client.add_guidance(pid, hint)

    # 4. Add golden test cases
    test_cases = [
        {"name": name, "field_values": fv, "expected_outcome": outcome}
        for name, fv, outcome in GOLDEN_CASES
    ]
    client.add_tests(pid, test_cases)

    # 5. Trigger generation
    job = client.generate(pid)

    # 6. Poll until done
    deadline = time.time() + GENERATION_TIMEOUT
    while time.time() < deadline:
        status = client.get_status(pid)
        job_info = status.get("job") or {}
        job_status = job_info.get("status", "unknown")

        if job_status == "success":
            bundle_id = status.get("latest_bundle_id")
            # 7. Publish bundle (sets status="active" so /schema, /decide, /test-run work)
            client.publish(pid)
            return {
                "project_id": pid,
                "bundle_id": bundle_id,
                "client": client,
            }

        if job_status == "failed":
            pytest.fail(
                f"Generation failed: {job_info.get('error_message', 'unknown')}"
            )

        time.sleep(POLL_INTERVAL)

    pytest.fail("Generation timed out after 5 minutes")


# ---------------------------------------------------------------------------
# Tests: generation & structure
# ---------------------------------------------------------------------------


class TestSpacecraftGeneration:
    """Verify the generation pipeline completes and produces valid output."""

    def test_bundle_id_exists(self, spacecraft_bundle):
        assert spacecraft_bundle["bundle_id"], "No bundle_id returned after generation"

    def test_schema_has_sufficient_fields(self, spacecraft_bundle):
        """Generated bundle must have ≥5 input fields."""
        client = spacecraft_bundle["client"]
        schema = client.get_schema(spacecraft_bundle["bundle_id"])
        fields = schema.get("fields", [])
        assert len(fields) >= 5, f"Expected ≥5 fields, got {len(fields)}: {[f['field_id'] for f in fields]}"

    def test_schema_field_types(self, spacecraft_bundle):
        """Verify field types include bool, int, and enum."""
        client = spacecraft_bundle["client"]
        schema = client.get_schema(spacecraft_bundle["bundle_id"])
        types = {f["field_type"].lower() for f in schema.get("fields", [])}
        assert "bool" in types, f"Expected bool fields, got types: {types}"


# ---------------------------------------------------------------------------
# Tests: golden outcomes via /decide
# ---------------------------------------------------------------------------


class TestSpacecraftDecisions:
    """Verify known outcomes via the /decide endpoint."""

    def test_vogon_not_eligible(self, spacecraft_bundle):
        client = spacecraft_bundle["client"]
        result = client.decide(spacecraft_bundle["bundle_id"], {"space.crew.species": "Vogon"})
        assert result["decision"] == "not_eligible", f"Vogon should be not_eligible, got {result['decision']}"

    def test_full_compliance_eligible(self, spacecraft_bundle):
        client = spacecraft_bundle["client"]
        result = client.decide(spacecraft_bundle["bundle_id"], {
            "space.crew.species": "Human",
            "space.crew.flight_hours": 600,
            "space.crew.has_pilot_license": True,
            "space.crew.has_gaa_exam": True,
            "space.crew.has_approved_provider_cert": True,
            "space.crew.has_radiation_cert": True,
            "space.medical.cert_valid": True,
            "space.crew.age": 35,
            "space.mission.type": "suborbital",
            "space.vessel.propulsion_type": "conventional",
            "space.crew.has_towel": True,
        })
        assert result["decision"] == "eligible", f"Full compliance should be eligible, got {result['decision']}"

    def test_no_towel_not_eligible(self, spacecraft_bundle):
        client = spacecraft_bundle["client"]
        result = client.decide(spacecraft_bundle["bundle_id"], {
            "space.crew.species": "Human",
            "space.crew.flight_hours": 600,
            "space.crew.has_pilot_license": True,
            "space.crew.has_gaa_exam": True,
            "space.medical.cert_valid": True,
            "space.crew.age": 35,
            "space.mission.type": "suborbital",
            "space.crew.has_towel": False,
        })
        assert result["decision"] == "not_eligible", f"No towel should be not_eligible, got {result['decision']}"

    def test_orbital_no_radiation_not_eligible(self, spacecraft_bundle):
        client = spacecraft_bundle["client"]
        result = client.decide(spacecraft_bundle["bundle_id"], {
            "space.crew.species": "Human",
            "space.crew.flight_hours": 600,
            "space.crew.has_pilot_license": True,
            "space.crew.has_gaa_exam": True,
            "space.medical.cert_valid": True,
            "space.crew.age": 35,
            "space.mission.type": "orbital",
            "space.crew.has_radiation_cert": False,
            "space.crew.has_towel": True,
        })
        assert result["decision"] == "not_eligible", (
            f"Orbital + no radiation cert should be not_eligible, got {result['decision']}"
        )

    def test_age_exemption_eligible(self, spacecraft_bundle):
        client = spacecraft_bundle["client"]
        result = client.decide(spacecraft_bundle["bundle_id"], {
            "space.crew.species": "Human",
            "space.crew.age": 65,
            "space.crew.has_gaa_exam": True,
            "space.crew.has_approved_provider_cert": True,
            "space.crew.has_radiation_cert": True,
            "space.medical.cert_valid": True,
            "space.mission.type": "suborbital",
            "space.vessel.propulsion_type": "conventional",
            "space.crew.has_towel": True,
        })
        assert result["decision"] == "eligible", (
            f"Age exemption (65) should be eligible, got {result['decision']}"
        )


# ---------------------------------------------------------------------------
# Tests: test-run endpoint (pass rate)
# ---------------------------------------------------------------------------


class TestSpacecraftTestRun:
    """Verify the /test-run endpoint returns acceptable pass rate."""

    def test_run_returns_results(self, spacecraft_bundle):
        client = spacecraft_bundle["client"]
        result = client.run_tests(spacecraft_bundle["project_id"])
        assert result["total"] >= 5, f"Expected ≥5 test cases, got {result['total']}"

    def test_pass_rate_at_least_80_percent(self, spacecraft_bundle):
        """Binding assertion: ≥80% of golden cases must pass via /test-run."""
        client = spacecraft_bundle["client"]
        result = client.run_tests(spacecraft_bundle["project_id"])
        total = result["total"]
        passed = result["passed"]
        pass_rate = passed / total if total > 0 else 0

        details = []
        for r in result.get("results", []):
            status = "PASS" if r["passed"] else "FAIL"
            details.append(f"  {status} [{r['name']}]: expected={r.get('expected')}, actual={r.get('actual')}")

        assert pass_rate >= 0.8, (
            f"Pass rate {pass_rate:.0%} ({passed}/{total}) below 80% threshold.\n"
            + "\n".join(details)
        )
