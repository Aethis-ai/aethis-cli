# aethis-cli

CLI for the [Aethis](https://aethis.ai) developer API — evaluate eligibility, author rule bundles, and publish from the terminal.

## Install

```bash
pip install aethis-cli
```

## Quick start

No sign-up needed. Decision tools work immediately.

```bash
# Evaluate eligibility against a published bundle
aethis decide -b <bundle_id> -i '{"space.crew.age": 35, "space.medical.cert_valid": true}'

# Inspect the input fields a bundle expects
aethis fields -b <bundle_id>

# Get human-readable rule descriptions
aethis explain -b <bundle_id>
```

## Author your own rules

Authoring requires an API key. Access is rolling out now — [register interest](https://aethis.ai/dashboard).

```bash
# 1. Authenticate (creates a key via browser sign-in)
aethis account generate
# Or paste an existing key: aethis login

# 2. Initialise a project
mkdir my-rules && cd my-rules
aethis init

# 3. Add source documents and guidance
#    (put PDFs/text in .aethis/sources/, hints in .aethis/guidance/hints.yaml)

# 4. Generate a rule bundle
aethis generate

# 5. Run test cases
aethis test

# 6. Publish the bundle
aethis publish
```

## Try the example

A complete, runnable example is included in `examples/spacecraft-crew-rules/`:

```bash
cp -r examples/spacecraft-crew-rules my-first-rules && cd my-first-rules
aethis account generate
aethis generate --poll
aethis test
aethis decide -i '{"space.crew.species": "Human", "space.crew.age": 35, "space.crew.flight_hours": 600, "space.crew.has_pilot_license": true, "space.crew.has_gaa_exam": true, "space.medical.cert_valid": true, "space.mission.type": "suborbital", "space.crew.has_towel": true}'
```

See `examples/spacecraft-crew-rules/README.md` for details.

## Commands

| Command | Description |
|---------|-------------|
| `aethis account generate` | Create a new API key (browser sign-in) |
| `aethis account keys` | List your API keys (masked) |
| `aethis account revoke` | Revoke an API key by ID |
| `aethis account permissions` | List canonical API permission mappings |
| `aethis login` | Paste an existing API key |
| `aethis init` | Initialise a new project in the current directory |
| `aethis generate` | Upload sources + guidance, trigger generation, poll until done |
| `aethis status` | Check generation job progress |
| `aethis test` | Run test cases against the generated bundle |
| `aethis publish` | Set the bundle as active |
| `aethis fields` | Show input fields for the current bundle |
| `aethis explain` | Show human-readable rule descriptions |
| `aethis decide` | Evaluate eligibility with a JSON input |

## Project structure

After `aethis init`, your project looks like:

```
my-rules/
  .aethis/
    aethis.yaml          # project config
    state.json           # tracked IDs (project, bundle, job)
    sources/             # PDF/text source documents
    guidance/
      hints.yaml         # guidance hints for the code synthesizer
    tests/
      scenarios.yaml     # golden test cases
```

### aethis.yaml

```yaml
project: my-rules
api_key_env: AETHIS_API_KEY
base_url: https://api.aethis.ai
```

### scenarios.yaml

```yaml
tests:
  - name: "Eligible — all requirements met"
    inputs:
      space.crew.age: 35
      space.crew.flight_hours: 600
      space.crew.has_pilot_license: true
    expect:
      outcome: eligible
  - name: "Not eligible — no medical cert"
    inputs:
      space.medical.cert_valid: false
    expect:
      outcome: not_eligible
```

## Environment variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `AETHIS_API_KEY` | Your API key (`ak_live_...`) | Authoring only | — |
| `AETHIS_BASE_URL` | API base URL | No | `https://api.aethis.ai` |
| `AETHIS_CLERK_DOMAIN` | Clerk domain override (development only) | No | `clerk.aethis.legal` |

## Internal admin workflows

IAM administration commands have moved to the internal `aethis-admin` tool.

## Development

```bash
git clone https://github.com/aethis-ai/aethis-cli.git
cd aethis-cli
pip install -e ".[dev]"
pytest tests/ -v
```

## Shell completions

```bash
# Install tab completion for your shell
aethis --install-completion bash   # or zsh, fish, powershell
```

## Benchmarks

See how the engine compares to frontier LLMs on real-world eligibility rules: [aethis-examples](https://github.com/Aethis-ai/aethis-examples)

## License

MIT
