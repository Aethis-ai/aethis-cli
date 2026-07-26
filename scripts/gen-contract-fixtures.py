#!/usr/bin/env python3
"""Regenerate the CLI's contract fixtures from real wire payloads.

The fixtures under ``tests/fixtures/contract/`` are captured, never
hand-written. A hand-written approximation of a response shape is worse than
no fixture at all: it passes, forever, against a payload the CLI could never
have read off the wire.

Two sources, both authoritative:

1. **A live engine** (default: staging) for every payload a public caller can
   provoke -- terminal decisions, blocking input errors, an incomplete
   evaluation, the 422 for an undeclared request member, and the explain
   envelope. Captured verbatim.

2. **The engine's own response models**, for payloads a public caller
   cannot provoke against staging today -- the citation-bearing responses and
   the rulebook decision envelope (no public rulebook is published on
   staging, so ``/rulebooks/`` returns an empty catalogue). No published showcase ruleset carries source references yet, so
   the DTO cannot be captured from the wire; instead the real model
   serialises the fixture (it rejects a malformed digest, a non-HTTPS URL or
   a missing member exactly as the server would), and the references are
   grafted onto captured payloads the same way ``aethis-core``'s decide route
   does -- ``explanation.groups[].criteria[].source_references`` for decide,
   ``criteria[].source_references`` for explain.

Usage (non-interactive, bounded timeouts, no prompts):

    uv run python scripts/gen-contract-fixtures.py --capture
    uv run --project ../aethis-core python scripts/gen-contract-fixtures.py --cite --rulebook
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "contract"
DEFAULT_BASE = "https://staging.api.aethis.ai"
SHOWCASE = "aethis/spacecraft-crew-certification"
TIMEOUT = 30

VALID_INPUTS = {
    "space.crew.species": "Human",
    "space.crew.flight_hours": 1200,
    "space.crew.age": 34,
    "space.medical.cert_valid": True,
    "space.mission.type": "orbital",
    "space.crew.has_radiation_cert": True,
    "space.crew.has_towel": True,
    "space.vessel.propulsion_type": "Bistromathics",
    "space.crew.has_pilot_license": True,
    "space.crew.has_gaa_exam": True,
    "space.crew.has_approved_provider_cert": True,
}


def _post(base: str, path: str, body: Dict[str, Any]) -> Any:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read())


def _get(base: str, path: str) -> Any:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read())


def _write(name: str, payload: Any) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / name).write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {name}")


def capture(base: str) -> None:
    """Capture every publicly provokable payload class from a live engine."""
    _write(
        "decide_terminal_eligible.json",
        _post(
            base,
            "/api/v1/public/decide",
            {
                "ruleset_id": SHOWCASE,
                "field_values": VALID_INPUTS,
                "include_explanation": True,
                "include_trace": True,
            },
        ),
    )
    _write(
        "decide_terminal_not_eligible.json",
        _post(
            base,
            "/api/v1/public/decide",
            {
                "ruleset_id": SHOWCASE,
                "field_values": {**VALID_INPUTS, "space.crew.species": "Vogon"},
                "include_explanation": True,
            },
        ),
    )
    _write(
        "decide_blocking_unknown_field.json",
        _post(
            base,
            "/api/v1/public/decide",
            {
                "ruleset_id": SHOWCASE,
                "field_values": {**VALID_INPUTS, "space.crew.favourite_biscuit": "hobnob"},
                "include_explanation": True,
            },
        ),
    )
    _write(
        "decide_blocking_bad_value.json",
        _post(
            base,
            "/api/v1/public/decide",
            {
                "ruleset_id": SHOWCASE,
                "field_values": {
                    "space.crew.species": "Human",
                    "space.crew.flight_hours": "loads",
                    "space.crew.age": 34,
                },
            },
        ),
    )
    _write(
        "decide_blocking_enum_value.json",
        _post(
            base,
            "/api/v1/public/decide",
            {
                "ruleset_id": SHOWCASE,
                "field_values": {**VALID_INPUTS, "space.vessel.propulsion_type": "chemical"},
                "include_explanation": True,
                "include_trace": True,
            },
        ),
    )
    _write(
        "decide_undetermined_incomplete.json",
        _post(
            base,
            "/api/v1/public/decide",
            {"ruleset_id": SHOWCASE, "field_values": {"space.crew.species": "Human"}},
        ),
    )
    # An undeclared top-level request member: the API rejects it rather than
    # ignoring it, so a caller can never mistake unimplemented surface for
    # supported surface.
    _write(
        "decide_422_unknown_request_key.json",
        _post(
            base,
            "/api/v1/public/decide",
            {
                "ruleset_id": SHOWCASE,
                "field_values": {"space.crew.species": "Human"},
                "batch": [{"field_values": {}}],
            },
        ),
    )
    _write("explain_envelope.json", _get(base, f"/api/v1/public/rulesets/{SHOWCASE}/explain"))


def _showcase_references() -> list:
    """Serialise showcase source references through the engine's own model."""
    from aethis_core.public.models.source_reference import SourceQuote, SourceReference

    quote = (
        "A vessel is propulsion-compliant where it operates an Infinite "
        "Improbability Drive, a Bistromathics drive, or a Heart of Gold Special."
    )
    digest = "sha256:" + hashlib.sha256(quote.encode()).hexdigest()
    reference = SourceReference(
        source_id="SHOWCASE-SPACECRAFT#S7/P2",
        title="Spacecraft Crew Certification — showcase source",
        authority="Aethis showcase catalogue",
        url="https://docs.aethis.ai/showcase/spacecraft-crew-certification",
        locator="Section 7, paragraph 2",
        source_version="2026-07-01",
        source_date="2026-07-01",
        content_digest=digest,
        licence="CC-BY-4.0",
        verified_at="2026-07-25T12:00:00Z",
        quote=SourceQuote(
            exact=quote,
            prefix="Propulsion compliance. ",
            suffix=" Any other propulsion type requires a waiver.",
        ),
        media_type="html",
        deep_link=(
            "https://docs.aethis.ai/showcase/spacecraft-crew-certification"
            "#:~:text=A%20vessel%20is%20propulsion%2Dcompliant"
        ),
    )
    return [reference.model_dump(mode="json")]


def cite() -> None:
    """Graft engine-serialised references onto the captured payloads."""
    references = _showcase_references()
    target_criterion = "propulsion_type"

    decide = json.loads((FIXTURES / "decide_terminal_not_eligible.json").read_text())
    attached = 0
    # Mirrors aethis-core's decide route: references are attached per
    # criterion inside explanation.groups[].criteria[].
    for group in decide.get("explanation", {}).get("groups", []) or []:
        for criterion in group.get("criteria", []) or []:
            if criterion.get("criterion_id") == target_criterion:
                criterion["source_references"] = references
                attached += 1
    if not attached:
        sys.exit(f"criterion {target_criterion!r} not present in the captured decide explanation")
    _write("decide_with_source_references.json", decide)

    explain = json.loads((FIXTURES / "explain_envelope.json").read_text())
    attached = 0
    for criterion in explain.get("criteria", []) or []:
        if criterion.get("criterion_id") == target_criterion:
            criterion["source_references"] = references
            attached += 1
    if not attached:
        sys.exit(f"criterion {target_criterion!r} not present in the captured explain envelope")
    _write("explain_with_source_references.json", explain)

    # A reference that reached the caller degraded (an optional locator gone
    # AND a required member missing) -- the CLI must show the gap, not render
    # a confident-looking citation with holes in it.
    degraded = json.loads(json.dumps(explain))
    for criterion in degraded.get("criteria", []) or []:
        for reference in criterion.get("source_references") or []:
            reference.pop("deep_link", None)
            reference.pop("licence", None)
            reference.pop("locator", None)
    _write("explain_with_degraded_source_reference.json", degraded)


def rulebook() -> None:
    """Serialise a blocked rulebook decision through the engine's own model.

    No public rulebook is published on staging, so this envelope cannot be
    captured from the wire. `DecideResponse` is the model the route returns,
    and it enforces the envelope the CLI has to survive: the `decision`
    literal, the required replay fields, and -- for a rulebook -- an
    unresolved `ruleset_version` with no `content_digest`, which is legal for
    a composite and must NOT be reported as a contract violation.

    Values are anchored to a real captured ruleset decision so nothing is
    invented beyond the rulebook-vs-ruleset envelope difference itself.
    """
    from aethis_core.public.routes.decide import DecideResponse

    anchor = json.loads((FIXTURES / "decide_blocking_unknown_field.json").read_text())
    response = DecideResponse(
        decision_id=anchor["decision_id"],
        inputs_hash=anchor["inputs_hash"],
        decision_time=anchor["decision_time"],
        engine_version=anchor["engine_version"],
        decision="undetermined",
        rulebook_id="aethis/spacecraft-certification-book",
        ruleset_version="unknown",
        fields_evaluated=anchor["fields_evaluated"],
        fields_provided=anchor["fields_provided"],
        missing_fields=anchor["missing_fields"],
        field_errors=anchor["field_errors"],
        section_results=[{"section_id": "crew", "status": "pending"}],
    )
    _write("decide_rulebook_blocking.json", response.model_dump(mode="json"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--capture", action="store_true", help="capture live payloads")
    parser.add_argument("--cite", action="store_true", help="graft engine-serialised source references")
    parser.add_argument("--rulebook", action="store_true", help="serialise the rulebook decision envelope")
    args = parser.parse_args()
    if not (args.capture or args.cite or args.rulebook):
        parser.error("pass --capture, --cite and/or --rulebook")
    if args.capture:
        capture(args.base_url)
    if args.cite:
        cite()
    if args.rulebook:
        rulebook()


if __name__ == "__main__":
    main()
