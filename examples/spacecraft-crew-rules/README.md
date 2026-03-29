# Spacecraft Crew Certification — Example Project

This is a complete, runnable example using the fictional **Spacecraft Crew Certification Act 2049**.

The source document exercises every pattern the Aethis eligibility engine supports: species disqualification, multi-field AND, multi-route OR, age exemptions, conditional radiation requirements, and towel compliance.

## Run it

```bash
# Copy this example into your own project directory
cp -r examples/spacecraft-crew-rules my-first-rules
cd my-first-rules

# Authenticate (you need an Aethis API key)
aethis login

# Generate a rule bundle from the source document + guidance hints
aethis generate --poll

# Run the 5 golden test cases
aethis test

# Try an eligibility check
aethis decide -i '{"space.crew.species": "Human", "space.crew.age": 35, "space.crew.flight_hours": 600, "space.crew.has_pilot_license": true, "space.crew.has_gaa_exam": true, "space.medical.cert_valid": true, "space.mission.type": "suborbital", "space.crew.has_towel": true}'

# Try a failing case (Vogon species)
aethis decide -i '{"space.crew.species": "Vogon"}'
```

## What's inside

| File | Purpose |
|------|---------|
| `aethis.yaml` | Project configuration |
| `sources/spacecraft-crew-certification-act.md` | The source legislation (input to the code synthesiser) |
| `guidance/hints.yaml` | 8 prescriptive hints that guide DSL code generation |
| `tests/scenarios.yaml` | 5 golden test cases with expected outcomes |
