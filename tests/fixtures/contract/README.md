# Contract fixtures

Every payload here was **captured**, not written. A hand-written approximation
of a response shape is worse than no fixture: it passes forever against
something the CLI could never have read off the wire.

Regenerate with `scripts/gen-contract-fixtures.py` (non-interactive, bounded
timeouts):

```bash
uv run python scripts/gen-contract-fixtures.py --capture           # live engine
uv run --project ../aethis-core python scripts/gen-contract-fixtures.py --cite
```

## Captured from a live engine

Source: `https://staging.api.aethis.ai`, engine `aethis-core@0.48.0`, ruleset
`aethis/spacecraft-crew-certification` (`v1`,
`sha256:2abb79e2…`), captured 2026-07-26. The showcase catalogue is public and
carries no personal data.

| File | What it pins |
|---|---|
| `decide_terminal_eligible.json` | A terminal `eligible` result with resolved identity, trace and explanation. |
| `decide_terminal_not_eligible.json` | A terminal `not_eligible` result. |
| `decide_blocking_unknown_field.json` | An input key the ruleset does not define: blocking `field_errors`, `decision: undetermined`. |
| `decide_blocking_bad_value.json` | A known key whose value does not convert to the field's type. |
| `decide_blocking_enum_value.json` | An out-of-set enum value, with `include_trace` — the trace survives alongside the blocking error. |
| `decide_undetermined_incomplete.json` | Valid but insufficient inputs: `undetermined` with no `field_errors` and advisory guidance. |
| `decide_422_unknown_request_key.json` | The 422 envelope for an undeclared top-level request member (a fictional `batch`). |
| `explain_envelope.json` | The explain envelope: identity plus a **flat** `criteria` array. |

Note the shape difference the fixtures deliberately preserve: `/decide` nests
criteria under `explanation.groups[].criteria[]`; `/explain` returns a flat
`criteria` array. A fixture that served the same shape on both paths would let
a reader pass while being unable to read a real decide response.

## Serialised by the engine's own model

No published showcase ruleset carries source references yet, so the
`SourceReference` DTO cannot be captured from the wire. Instead the engine's
own pydantic model serialises it — the same validation the server applies
(digest pattern, HTTPS URL, required members, verbatim quote) — and the
references are grafted onto captured payloads exactly where the decide route
attaches them.

| File | What it pins |
|---|---|
| `decide_with_source_references.json` | References nested at `explanation.groups[].criteria[].source_references`. |
| `explain_with_source_references.json` | The identical DTO at `criteria[].source_references`. |
| `explain_with_degraded_source_reference.json` | A reference that arrived incomplete — the CLI must show the gap. |
| `engine_explain_source_contract.json` | Verbatim copy of `aethis-core/tests/contract/fixtures/explain_source_contract.json`: the OpenAPI contract pin the CLI's expectations are checked against. |

Re-copy the last one whenever the engine's contract fixture changes; a drift
test fails loudly if the CLI's assumptions and the engine's contract diverge.
