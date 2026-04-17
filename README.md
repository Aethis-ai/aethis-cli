# aethis-cli

[![PyPI](https://img.shields.io/pypi/v/aethis-cli.svg)](https://pypi.org/project/aethis-cli/)
[![Docs](https://img.shields.io/badge/docs-docs.aethis.ai-blue)](https://docs.aethis.ai)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

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

### Decision (no API key required)

| Command | Description |
|---------|-------------|
| `aethis decide -b <bundle_id> -i '<json>'` | Evaluate eligibility. Add `--explain` for trace output. |
| `aethis fields -b <bundle_id>` | Show input fields the bundle expects |
| `aethis explain -b <bundle_id>` | Human-readable rule descriptions |

### Authoring

| Command | Description |
|---------|-------------|
| `aethis init` | Initialise a new project in the current directory |
| `aethis generate [--poll]` | Upload sources + guidance, trigger generation |
| `aethis status` | Check generation job progress |
| `aethis test` | Run test cases against the latest bundle |
| `aethis publish [--force]` | Set the latest bundle as active |
| `aethis source` | Fetch the DSL source for a published bundle |

### Guidance (project-level)

Project guidance lives in `.aethis/guidance/hints.yaml` and is uploaded by `aethis generate`. These commands manage hints server-side after upload.

| Command | Description |
|---------|-------------|
| `aethis guidance list` | List active hints for the current project |
| `aethis guidance export <file>` | Export the current project's hints to YAML |
| `aethis guidance import <file>` | Import hints from a YAML file |
| `aethis guidance deactivate <hint_id>` | Deactivate a specific hint |

### Domain guidance

Domain-level hints apply to every project in a given domain.

| Command | Description |
|---------|-------------|
| `aethis domain guidance add <domain> "<text>"` | Add a domain-level hint |
| `aethis domain guidance list <domain>` | List hints for a domain |
| `aethis domain guidance import <domain> <file>` | Import hints from a YAML file |
| `aethis domain guidance export <domain> <file>` | Export domain hints to YAML |

### Projects & bundles

| Command | Description |
|---------|-------------|
| `aethis projects list` | List your projects |
| `aethis projects show <project_id>` | Show project details |
| `aethis projects archive <project_id>` | Archive a project |
| `aethis bundles list` | List published bundles |
| `aethis bundles archive <bundle_id>` | Archive a bundle |

### Account

| Command | Description |
|---------|-------------|
| `aethis account generate` | Create an API key via browser sign-in |
| `aethis account keys` | List your API keys (masked) |
| `aethis account revoke <key_id>` | Revoke a key |
| `aethis account permissions` | Show the permissions your current key has |
| `aethis login` | Paste an existing key |

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

## Troubleshooting

**`aethis generate` times out but server continues**
The server finishes even if your client disconnects. Wait 10–15 min, then run `aethis bundles list` — if the bundle appeared, run `aethis test` and `aethis publish`. Do not re-trigger generation; that creates a duplicate run.

**`aethis publish` fails with "tests are failing"**
`publish` refuses a bundle with failing tests. Fix with guidance + regenerate, or pass `--force` (not recommended for production).

**422 Validation error on `aethis decide`**
`DATE` fields must be passed as **integer ordinals**, not ISO strings. `2025-04-13` → `739354`:
```bash
python3 -c "from datetime import date; print(date(2025,4,13).toordinal())"
```

**`Auth error: …`**
Your API key is missing, expired, or revoked. Run `aethis login` to paste a new one, or `aethis account generate` to create one.

**`403 Forbidden: missing scope`**
Your key lacks the required scope for the command. `aethis account permissions` shows what your current key can do. Contact support to upgrade scopes.

## Benchmarks

See how the engine compares to frontier LLMs on real-world eligibility rules: [aethis-examples](https://github.com/Aethis-ai/aethis-examples)

## License

MIT
