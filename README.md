# aethis-cli

CLI for the [Aethis](https://aethis.ai) developer API — author, test, and publish rule bundles from the terminal.

## Install

```bash
pip install aethis-cli
```

## Quick start

```bash
# 1. Authenticate
aethis login

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

# 7. Evaluate eligibility
aethis decide -i '{"applicant_age": 35, "flight_fitness_certified": true}'
```

## Commands

| Command | Description |
|---------|-------------|
| `aethis login` | Authenticate with the Aethis API |
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
  - name: "Eligible — experienced pilot"
    inputs:
      applicant_age: 35
      simulator_hours: 500
    expect:
      outcome: eligible
  - name: "Not eligible — no medical cert"
    inputs:
      flight_fitness_certified: false
    expect:
      outcome: not_eligible
```

## Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AETHIS_API_KEY` | Your API key (`ak_live_...`) | — |
| `AETHIS_BASE_URL` | API base URL | `https://api.aethis.ai` |

## Development

```bash
git clone https://github.com/aethis-ai/aethis-cli.git
cd aethis-cli
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
