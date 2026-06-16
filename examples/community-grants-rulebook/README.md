# Community Grants — rulebook + member rulesets

A small, generic example of the **hierarchical rulebook → ruleset** structure and
the field-authoring loop (`aethis fields discover / validate / pull`).

A borough runs two community grants. Both share the same eligibility gate —
*you must be a resident of the borough*. That field is defined **once**, at the
rulebook level, and propagates into every member ruleset (the rulebook wins on
shared keys), so an end user is only ever asked for it once.

```
community-grants/                 # kind: rulebook
  fields/fields.yaml              # SHARED: applicant.is_borough_resident
  guidance/hints.yaml             # SHARED: "open only to borough residents"
  rulesets/
    youth-activity-grant/         # kind: ruleset, rulebook: ../..   (resident + 16–24 age group)
    senior-wellbeing-grant/       # kind: ruleset, rulebook: ../..   (resident + aged 65 or over)
```

Each member ruleset declares its membership explicitly with the `rulebook:` key
in its `aethis.yaml`; directory position (`<rulebook>/rulesets/<child>/`) is the
fallback when the key is omitted. Each member's own `fields/fields.yaml` starts
empty — the shared field comes from the rulebook.

## Authoring loop (per member ruleset)

Run these from inside a ruleset directory, e.g. `rulesets/youth-activity-grant/`:

```bash
aethis fields discover     # propose ruleset-specific fields from the sources, merge into fields.yaml
aethis fields validate     # check fields.yaml: valid types, no duplicate keys, enum needs enum_values
aethis generate --poll     # uploads sources + the rulebook's shared field/guidance, then generates
aethis fields pull          # sync the produced field definitions back into fields.yaml
```

After `generate`, the shared `applicant.is_borough_resident` field appears in the
produced schema of **both** rulesets — even though neither ruleset declared it
locally — proving the rulebook field flowed down into each member. `aethis generate`
also prints a pinned-vs-produced field diff so any drift is visible.

## Automated end-to-end test

`tests/e2e/test_rulebook_hierarchy_e2e.py` drives this flow against a live Aethis
API and asserts the shared field propagates into both rulesets. It is gated by
the `manual` marker (skipped by default). Point it at a disposable backend — a
local `aethis-core` is ideal (it uses its own server-side LLM key, so no
`ANTHROPIC_API_KEY` is needed in the test process):

```bash
export AETHIS_BASE_URL=http://localhost:8080   # local aethis-core (DISABLE_AUTH)
export AETHIS_API_KEY=test
uv run pytest tests/e2e/test_rulebook_hierarchy_e2e.py -m manual -v -s
```

> The exact field *keys* a generation produces are LLM-chosen; strict enforcement
> that a produced ruleset uses the pinned rulebook key is a later phase
> (aethis-core#194). The deterministic rulebook-wins merge that this CLI performs
> is covered by the unit test `tests/test_generate_fields.py::test_upload_field_vocabulary_rulebook_wins`.
