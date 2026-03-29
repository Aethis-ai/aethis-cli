"""
English Language CLI E2E test — full pipeline via AethisClient.

Tests the complete CLI → API → CodeSynthesizer → eligibility engine pipeline using the
English language requirement for British naturalisation (BNA 1981, Sch 1,
Para 1(1)(c)) as source material.

The English language requirement has 9 OR routes — any single route satisfying
the requirement makes the applicant eligible. This tests a fundamentally
different DSL pattern from the spacecraft test (which uses AND across groups
with IMPLIES/OR within groups).

Routes:
  1. MESC nationality (national of a majority English speaking country)
  2. UK degree (Bachelor's+ taught in English)
  3. MESC degree + AQUALS (excl. Canada)
  4. Non-MESC/Canadian degree + AQUALS + ELPS
  5. SELT settlement reuse (B1+ used for ILR)
  6. SELT recent (B1+ within 2 years)
  7. Age exemption (under 18 or 65+)
  8. Medical exemption
  9. Discretionary waiver

Requires:
  AETHIS_API_KEY  — developer API key
  AETHIS_BASE_URL — API base URL (default: https://api.aethis.ai)

Run with:
  pytest tests/e2e/test_english_language_e2e.py -m manual -v -s
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

ENGLISH_LANG_PATH = (
    Path(__file__).resolve().parents[3]
    / "tda-server"
    / "docs"
    / "test-fixtures"
    / "english-language-requirement.md"
)

GENERATION_TIMEOUT = 300  # 5 minutes
POLL_INTERVAL = 5

# ---------------------------------------------------------------------------
# Guidance hints — prescriptive DSL structure for the English language req
# ---------------------------------------------------------------------------

GUIDANCE_HINTS = [
    # Field definitions
    (
        "Use these EXACT field keys: "
        "lang.nationality (Sort.ENUM: Australian, Canadian, American, Irish, Jamaican, Other), "
        "lang.age (Sort.INT), "
        "lang.has_uk_degree (Sort.BOOL), "
        "lang.has_mesc_degree (Sort.BOOL), "
        "lang.has_ecctis_aquals (Sort.BOOL), "
        "lang.has_ecctis_elps (Sort.BOOL), "
        "lang.selt_level (Sort.ENUM: none, A1, A2, B1, B2, C1, C2), "
        "lang.selt_used_for_settlement (Sort.BOOL), "
        "lang.selt_within_two_years (Sort.BOOL), "
        "lang.has_medical_exemption (Sort.BOOL), "
        "lang.discretion_applied (Sort.BOOL)"
    ),
    (
        "Generate FieldDefinition and Criterion objects. "
        "Use ONLY the group 'english_language'. "
        "ALL 9 routes are OR alternatives in a SINGLE group — any one route "
        "passing means the applicant is eligible. "
        "Do NOT create separate groups for each route."
    ),
    # MESC nationality route
    (
        "MESC nationality route (group='english_language'): Criterion: "
        "Op(IN, [FieldRef(key='lang.nationality'), "
        "Const(sort=Sort.ENUM, value=['Australian', 'Canadian', 'American', 'Irish', 'Jamaican'])]). "
        "Nationals of majority English speaking countries automatically satisfy the requirement."
    ),
    # UK degree route
    "UK degree route (group='english_language'): Criterion: lang.has_uk_degree EQ True",
    # MESC degree + AQUALS
    (
        "MESC degree route (group='english_language'): SINGLE Criterion: "
        "Op(AND, [Op(EQ, [FieldRef(key='lang.has_mesc_degree'), Const(sort=Sort.BOOL, value=True)]), "
        "Op(EQ, [FieldRef(key='lang.has_ecctis_aquals'), Const(sort=Sort.BOOL, value=True)])]). "
        "MESC country degree (excluding Canada) needs AQUALS only, no ELPS."
    ),
    # Non-MESC/Canadian degree + AQUALS + ELPS
    (
        "Non-MESC/Canadian degree route (group='english_language'): SINGLE Criterion: "
        "Op(AND, [Op(EQ, [FieldRef(key='lang.has_ecctis_aquals'), Const(sort=Sort.BOOL, value=True)]), "
        "Op(EQ, [FieldRef(key='lang.has_ecctis_elps'), Const(sort=Sort.BOOL, value=True)])]). "
        "Non-MESC country or Canadian degree needs BOTH AQUALS and ELPS."
    ),
    # SELT settlement reuse
    (
        "SELT settlement route (group='english_language'): SINGLE Criterion: "
        "Op(AND, [Op(IN, [FieldRef(key='lang.selt_level'), "
        "Const(sort=Sort.ENUM, value=['B1', 'B2', 'C1', 'C2'])]), "
        "Op(EQ, [FieldRef(key='lang.selt_used_for_settlement'), "
        "Const(sort=Sort.BOOL, value=True)])]). "
        "B1+ SELT used for settlement can be reused with no time limit."
    ),
    # SELT recent
    (
        "SELT recent route (group='english_language'): SINGLE Criterion: "
        "Op(AND, [Op(IN, [FieldRef(key='lang.selt_level'), "
        "Const(sort=Sort.ENUM, value=['B1', 'B2', 'C1', 'C2'])]), "
        "Op(EQ, [FieldRef(key='lang.selt_within_two_years'), "
        "Const(sort=Sort.BOOL, value=True)])]). "
        "B1+ SELT within last 2 years (not used for settlement)."
    ),
    # Age exemption
    (
        "Age exemption (group='english_language'): SINGLE Criterion: "
        "Op(OR, [Op(GE, [FieldRef(key='lang.age'), Const(sort=Sort.INT, value=65)]), "
        "Op(LT, [FieldRef(key='lang.age'), Const(sort=Sort.INT, value=18)])]). "
        "Under 18 or 65+ are exempt from the English language requirement."
    ),
    # Medical exemption
    "Medical exemption (group='english_language'): Criterion: lang.has_medical_exemption EQ True",
    # Discretionary waiver
    "Discretionary waiver (group='english_language'): Criterion: lang.discretion_applied EQ True",
    # Negative guidance
    (
        "IMPORTANT: Do NOT create separate groups for each route. "
        "ALL criteria must be in the SINGLE group 'english_language'. "
        "The group uses OR semantics — any single criterion passing means eligible. "
        "Creating separate groups would make ALL routes mandatory (AND across groups), "
        "which is wrong — the applicant only needs to satisfy ONE route."
    ),
]

# ---------------------------------------------------------------------------
# Golden test cases
# ---------------------------------------------------------------------------

GOLDEN_CASES: list[tuple[str, dict[str, Any], str]] = [
    (
        "Age exemption — applicant aged 70 is exempt (Section 7)",
        {
            "lang.nationality": "Other",
            "lang.age": 70,
            "lang.has_uk_degree": False,
            "lang.has_mesc_degree": False,
            "lang.has_ecctis_aquals": False,
            "lang.has_ecctis_elps": False,
            "lang.selt_level": "none",
            "lang.selt_used_for_settlement": False,
            "lang.selt_within_two_years": False,
            "lang.has_medical_exemption": False,
            "lang.discretion_applied": False,
        },
        "eligible",
    ),
    (
        "MESC national — Australian citizen satisfies automatically (Section 2)",
        {
            "lang.nationality": "Australian",
            "lang.age": 30,
            "lang.has_uk_degree": False,
        },
        "eligible",
    ),
    (
        "UK degree — Bachelor's from UK university (Section 3)",
        {
            "lang.nationality": "Other",
            "lang.age": 30,
            "lang.has_uk_degree": True,
        },
        "eligible",
    ),
    (
        "No evidence — Indian national, age 30, no qualifications (negative case)",
        {
            "lang.nationality": "Other",
            "lang.age": 30,
            "lang.has_uk_degree": False,
            "lang.has_mesc_degree": False,
            "lang.has_ecctis_aquals": False,
            "lang.has_ecctis_elps": False,
            "lang.selt_level": "none",
            "lang.selt_used_for_settlement": False,
            "lang.selt_within_two_years": False,
            "lang.has_medical_exemption": False,
            "lang.discretion_applied": False,
        },
        "not_eligible",
    ),
    (
        "SELT settlement — B1 used for ILR, reused for naturalisation (Section 6)",
        {
            "lang.nationality": "Other",
            "lang.age": 30,
            "lang.selt_level": "B1",
            "lang.selt_used_for_settlement": True,
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
def english_lang_bundle():
    """Run the full generate pipeline once, return project/bundle state."""
    client, base_url = _make_client()

    if not ENGLISH_LANG_PATH.exists():
        pytest.skip(f"Source doc not found: {ENGLISH_LANG_PATH}")

    # 1. Create project
    project = client.create_project(
        "english-lang-e2e-cli", "english_language", "uk_immigration",
    )
    pid = project["project_id"]

    # 2. Upload source document
    client.upload_sources(pid, [ENGLISH_LANG_PATH])

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


class TestEnglishLangGeneration:
    """Verify generation completes with valid output."""

    def test_bundle_id_exists(self, english_lang_bundle):
        assert english_lang_bundle["bundle_id"], "No bundle_id returned after generation"

    def test_schema_has_sufficient_fields(self, english_lang_bundle):
        """Generated bundle must have ≥5 input fields."""
        client = english_lang_bundle["client"]
        schema = client.get_schema(english_lang_bundle["bundle_id"])
        fields = schema.get("fields", [])
        assert len(fields) >= 5, (
            f"Expected ≥5 fields, got {len(fields)}: {[f['field_id'] for f in fields]}"
        )

    def test_schema_includes_expected_fields(self, english_lang_bundle):
        """Key fields like nationality, age, and has_uk_degree should be present."""
        client = english_lang_bundle["client"]
        schema = client.get_schema(english_lang_bundle["bundle_id"])
        field_ids = {f["field_id"] for f in schema.get("fields", [])}
        # At least nationality and age should be present (LLM may vary names slightly)
        has_nationality = any("national" in fid.lower() for fid in field_ids)
        has_age = any("age" in fid.lower() for fid in field_ids)
        assert has_nationality, f"No nationality field found in: {field_ids}"
        assert has_age, f"No age field found in: {field_ids}"


# ---------------------------------------------------------------------------
# Tests: golden outcomes via /decide
# ---------------------------------------------------------------------------


class TestEnglishLangDecisions:
    """Verify known outcomes via the /decide endpoint."""

    def test_age_exemption_eligible(self, english_lang_bundle):
        """Section 7: Applicant aged 70 is exempt from language requirement."""
        client = english_lang_bundle["client"]
        result = client.decide(english_lang_bundle["bundle_id"], {
            "lang.nationality": "Other",
            "lang.age": 70,
            "lang.has_uk_degree": False,
            "lang.has_mesc_degree": False,
            "lang.has_ecctis_aquals": False,
            "lang.has_ecctis_elps": False,
            "lang.selt_level": "none",
            "lang.selt_used_for_settlement": False,
            "lang.selt_within_two_years": False,
            "lang.has_medical_exemption": False,
            "lang.discretion_applied": False,
        })
        assert result["decision"] == "eligible", (
            f"Age 70 should be eligible (exemption), got {result['decision']}"
        )

    def test_mesc_national_eligible(self, english_lang_bundle):
        """Section 2: Australian national satisfies requirement automatically."""
        client = english_lang_bundle["client"]
        result = client.decide(english_lang_bundle["bundle_id"], {
            "lang.nationality": "Australian",
            "lang.age": 30,
            "lang.has_uk_degree": False,
        })
        assert result["decision"] == "eligible", (
            f"Australian national should be eligible, got {result['decision']}"
        )

    def test_uk_degree_eligible(self, english_lang_bundle):
        """Section 3: UK degree satisfies requirement."""
        client = english_lang_bundle["client"]
        result = client.decide(english_lang_bundle["bundle_id"], {
            "lang.nationality": "Other",
            "lang.age": 30,
            "lang.has_uk_degree": True,
        })
        assert result["decision"] == "eligible", (
            f"UK degree holder should be eligible, got {result['decision']}"
        )

    def test_no_evidence_not_eligible(self, english_lang_bundle):
        """No route satisfied → not_eligible."""
        client = english_lang_bundle["client"]
        result = client.decide(english_lang_bundle["bundle_id"], {
            "lang.nationality": "Other",
            "lang.age": 30,
            "lang.has_uk_degree": False,
            "lang.has_mesc_degree": False,
            "lang.has_ecctis_aquals": False,
            "lang.has_ecctis_elps": False,
            "lang.selt_level": "none",
            "lang.selt_used_for_settlement": False,
            "lang.selt_within_two_years": False,
            "lang.has_medical_exemption": False,
            "lang.discretion_applied": False,
        })
        assert result["decision"] == "not_eligible", (
            f"No evidence should be not_eligible, got {result['decision']}"
        )

    def test_selt_settlement_eligible(self, english_lang_bundle):
        """Section 6: B1 SELT used for settlement satisfies requirement."""
        client = english_lang_bundle["client"]
        result = client.decide(english_lang_bundle["bundle_id"], {
            "lang.nationality": "Other",
            "lang.age": 30,
            "lang.selt_level": "B1",
            "lang.selt_used_for_settlement": True,
        })
        assert result["decision"] == "eligible", (
            f"B1 SELT + settlement should be eligible, got {result['decision']}"
        )


# ---------------------------------------------------------------------------
# Tests: test-run endpoint (pass rate)
# ---------------------------------------------------------------------------


class TestEnglishLangTestRun:
    """Verify the /test-run endpoint returns acceptable pass rate."""

    def test_run_returns_results(self, english_lang_bundle):
        client = english_lang_bundle["client"]
        result = client.run_tests(english_lang_bundle["project_id"])
        assert result["total"] >= 5, f"Expected ≥5 test cases, got {result['total']}"

    def test_pass_rate_at_least_80_percent(self, english_lang_bundle):
        """Binding assertion: ≥80% of golden cases must pass via /test-run."""
        client = english_lang_bundle["client"]
        result = client.run_tests(english_lang_bundle["project_id"])
        total = result["total"]
        passed = result["passed"]
        pass_rate = passed / total if total > 0 else 0

        details = []
        for r in result.get("results", []):
            status = "PASS" if r["passed"] else "FAIL"
            details.append(
                f"  {status} [{r['name']}]: expected={r.get('expected')}, actual={r.get('actual')}"
            )

        assert pass_rate >= 0.8, (
            f"Pass rate {pass_rate:.0%} ({passed}/{total}) below 80% threshold.\n"
            + "\n".join(details)
        )
