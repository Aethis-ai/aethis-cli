# aethis-cli

[![PyPI](https://img.shields.io/pypi/v/aethis-cli.svg)](https://pypi.org/project/aethis-cli/)
[![Docs](https://img.shields.io/badge/docs-docs.aethis.ai-blue)](https://docs.aethis.ai)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

CLI for the [Aethis](https://aethis.ai) developer API — evaluate eligibility, author rule bundles, and publish from the terminal.

## Install

```bash
# Recommended — isolated, no venv juggling:
uv tool install aethis-cli

# Or, with pipx:
pipx install aethis-cli

# Or in a venv:
python -m venv .venv && source .venv/bin/activate
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

Rule authoring is **invite-only private beta**. Decision tools (`aethis decide`, `aethis fields`, `aethis explain`) work immediately with no sign-up — this section is for approved beta tenants. [Request access →](https://aethis.ai/sign-up)

```bash
# 1. Sign in (creates and stores an API key via browser)
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
```

## Try the example

A complete, runnable example is included in `examples/spacecraft-crew-rules/`:

```bash
cp -r examples/spacecraft-crew-rules my-first-rules && cd my-first-rules
aethis login
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

### Guidance (project-level)

Project guidance lives in `.aethis/guidance/hints.yaml` and is uploaded by `aethis generate`. These commands manage hints server-side after upload.

| Command | Description |
|---------|-------------|
| `aethis guidance list` | List active hints for the current project |
| `aethis guidance export <file>` | Export the current project's hints to YAML |
| `aethis guidance import <file>` | Import hints from a YAML file |
| `aethis guidance deactivate <hint_id>` | Deactivate a specific hint |

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
| `aethis login` | Sign in and store an API key locally (first-time setup) |
| `aethis account generate` | Mint an additional API key (rotation, multi-machine, scoped access) |
| `aethis account keys` | List your API keys (masked) |
| `aethis account revoke <key_id>` | Revoke a key |

## MCP one-liner

Wire up the [Aethis MCP server](https://github.com/Aethis-ai/aethis-mcp) in your AI editor without hand-editing JSON. Picks up the API key cached by `aethis login`, drops a canonical `aethis` server entry into the right config file, and preserves any other MCP servers you already have.

```bash
# One editor at a time
aethis mcp install --target cursor
aethis mcp install --target claude-code      # writes ./.mcp.json (project-local)
aethis mcp install --target claude-desktop
aethis mcp install --target windsurf

# Or all four at once
aethis mcp install --target all

# Reverse it (only removes the `aethis` entry, leaves others alone)
aethis mcp uninstall --target cursor
```

The command is idempotent — re-run it after `aethis login` rotates your key and the entry updates in place. Restart your editor to pick up the change.

| Target | Config path |
|--------|-------------|
| `claude-code` | `<cwd>/.mcp.json` (project-scoped) |
| `cursor` | `~/.cursor/mcp.json` |
| `claude-desktop` | macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` · Linux: `~/.config/Claude/claude_desktop_config.json` |
| `windsurf` | `~/.codeium/windsurf/mcp_config.json` |

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

## Extending with plugins

`aethis-cli` discovers third-party plugins via Python entry points under the `aethis_cli.plugins` group. A plugin is any installed package that exposes a `register(app: typer.Typer) -> None` callable; at startup the CLI calls it with the root Typer app and the plugin attaches extra commands.

Example `pyproject.toml`:

```toml
[project.entry-points."aethis_cli.plugins"]
my_plugin = "my_package.plugin:register"
```

Example `my_package/plugin.py`:

```python
import typer

def register(app: typer.Typer) -> None:
    @app.command()
    def hello() -> None:
        typer.echo("hello from my plugin")
```

Staff-only tools (DSL source viewer, IAM registry, domain-guidance management, `--base-url` override) live in the private `aethis-cli-internal` package, installed on request.

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
Your key lacks the required scope for the command. `aethis whoami` shows what your current key can do. Contact support to upgrade scopes.

## Benchmarks

See how the engine compares to frontier LLMs on real-world eligibility rules: [aethis-examples](https://github.com/Aethis-ai/aethis-examples)

## License

MIT
