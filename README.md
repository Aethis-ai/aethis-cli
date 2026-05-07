# aethis-cli

[![PyPI](https://img.shields.io/pypi/v/aethis-cli.svg)](https://pypi.org/project/aethis-cli/)
[![Docs](https://img.shields.io/badge/docs-docs.aethis.ai-blue)](https://docs.aethis.ai)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

CLI for the [Aethis](https://aethis.ai) developer API — evaluate eligibility, author rulesets, and publish from the terminal.

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

> **Authoring is in private beta.** Decision tools (`decide`, `fields`, `explain`) are public — no key required. Authoring tools (rule generation, test refinement, publishing) require an invite. Request access at [aethis.ai/developer-access](https://aethis.ai/developer-access).

No sign-up needed. Decision tools work immediately.

```bash
# Evaluate eligibility against a published ruleset
aethis decide -b <ruleset_id> -i '{"space.crew.age": 35, "space.medical.cert_valid": true}'

# Inspect the input fields a ruleset expects
aethis fields -b <ruleset_id>

# Get human-readable rule descriptions
aethis explain -b <ruleset_id>
```

## Authentication

Decision tools (`decide`, `fields`, `explain`) call public endpoints and never need a key. **Authoring tools** (`generate`, `publish`, `projects list`, etc.) need an API key — and the CLI manages that for you.

Authoring is in private beta — `aethis login` will only complete for invited developers. Request access at [aethis.ai/developer-access](https://aethis.ai/developer-access).

There are three ways the key can arrive:

**1. Explicit sign-in** (the canonical first-time setup):

```bash
aethis login
```

Opens your browser, completes Clerk OAuth, mints a fresh `ak_live_...` key, and stores it at `~/.config/aethis/credentials`. One-time per machine.

**2. Lazy auth** (the default — since v0.6.0):

If you skip step 1 and run an authenticated command directly, you'll get an inline prompt:

```
$ aethis init
No API key found. Open browser to sign in? [Y/n]
```

Hit enter, the browser flow runs, the command resumes. Same effect as `aethis login` followed by your original command, in one step.

**3. CI / scripts** — `--no-prompt`:

For any non-interactive context (`stdin` not a TTY, CI runners, piped commands) the CLI auto-skips the prompt and exits with a clear `AuthRequired` error. To force this behaviour even on a TTY:

```bash
aethis --no-prompt projects list
```

Pass `--api-key <ak_live_...>` to bypass the cache entirely (useful when you want to test a key without committing to it).

Manage existing keys with `aethis account keys` (list, masked) and `aethis account revoke <key_id>` (revoke). `aethis account generate` mints an *additional* key — for rotation, multi-machine setups, or scoped access. For first-time setup just use `aethis login`.

## Author your own rules (private beta)

Rule authoring is **invite-only private beta**. Decision tools (`aethis decide`, `aethis fields`, `aethis explain`) work immediately with no sign-up — this section is for approved beta tenants. [Request access →](https://aethis.ai/developer-access)

<!-- aethis-bible: public-messaging.md#5-how-rule-authoring-works -->
The authoring workflow follows a four-stage loop: discover, synthesise, test, publish. A ruleset cannot be published with failing tests.

```bash
# 1. Initialise a project (no-arg form prompts for a name; auto-runs `aethis login` if needed)
aethis init

# 2. Add source documents and guidance
#    (put PDFs/text in .aethis/sources/, hints in .aethis/guidance/hints.yaml)

# 3. Generate a rule ruleset
aethis generate

# 4. Run test cases
aethis test

# 5. Publish the ruleset
aethis publish
```

`aethis init` runs as a wizard: prompts for a project name (default = current directory), runs sign-in if you're not authed yet, scaffolds `.aethis/`, and prints the next-step ladder. Pass `--no-prompt` for scripted use (it'll fail fast on missing values rather than prompt).

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
| `aethis decide -b <ruleset_id> -i '<json>'` | Evaluate eligibility. Add `--explain` for trace output. |
| `aethis fields -b <ruleset_id>` | Show input fields the ruleset expects |
| `aethis explain -b <ruleset_id>` | Human-readable rule descriptions |

### Authoring

| Command | Description |
|---------|-------------|
| `aethis init` | Initialise a new project in the current directory |
| `aethis generate [--poll]` | Upload sources + guidance, trigger generation |
| `aethis status` | Check generation job progress |
| `aethis test` | Run test cases against the latest ruleset |
| `aethis publish [--force]` | Set the latest ruleset as active |

### Guidance (project-level)

Project guidance lives in `.aethis/guidance/hints.yaml` and is uploaded by `aethis generate`. These commands manage hints server-side after upload.

| Command | Description |
|---------|-------------|
| `aethis guidance list` | List active hints for the current project |
| `aethis guidance export <file>` | Export the current project's hints to YAML |
| `aethis guidance import <file>` | Import hints from a YAML file |
| `aethis guidance deactivate <hint_id>` | Deactivate a specific hint |

### Projects & rulesets

| Command | Description |
|---------|-------------|
| `aethis projects list` | List your projects |
| `aethis projects show <project_id>` | Show project details |
| `aethis projects archive <project_id>` | Archive a project |
| `aethis rulesets list` | List published rulesets |
| `aethis rulesets archive <ruleset_id>` | Archive a ruleset |

### Account

| Command | Description |
|---------|-------------|
| `aethis login` | Sign in and store an API key locally (first-time setup) |
| `aethis account generate` | Mint an additional API key (rotation, multi-machine, scoped access) |
| `aethis account keys` | List your API keys (masked) |
| `aethis account revoke <key_id>` | Revoke a key |

## MCP one-liner

Wire up the [Aethis MCP server](https://github.com/Aethis-ai/aethis-mcp) in your AI editor without hand-editing JSON. Picks up the API key cached by `aethis login`, drops a canonical `aethis` server entry into the right config file, and preserves any other MCP servers you already have.

> Onboarding an AI coding agent end-to-end? See [docs.aethis.ai/agents/onboarding](https://docs.aethis.ai/agents/onboarding) — install + verify + auth + workflow patterns in one page.

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
    state.json           # tracked IDs (project, ruleset, job)
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
| `AETHIS_API_KEY` | Your API key (`ak_live_...`). Bypasses the cached credential. | Authoring only | — |
| `AETHIS_BASE_URL` | Override the API host (staff/dev use; staging or self-hosted). | No | `https://api.aethis.ai` |
| `ANTHROPIC_API_KEY` | Forwarded per-request to the generation endpoint when running `aethis generate`. Never stored server-side. | Authoring only | — |

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
The server finishes even if your client disconnects. Wait 10–15 min, then run `aethis rulesets list` — if the ruleset appeared, run `aethis test` and `aethis publish`. Do not re-trigger generation; that creates a duplicate run.

**`aethis publish` fails with "tests are failing"**
`publish` refuses a ruleset with failing tests. Fix with guidance + regenerate, or pass `--force` (not recommended for production).

**422 Validation error on `aethis decide`**
`DATE` fields must be passed as **integer ordinals**, not ISO strings. `2025-04-13` → `739354`:
```bash
python3 -c "from datetime import date; print(date(2025,4,13).toordinal())"
```

**`Auth error: …`**
Your API key is missing, expired, or revoked. Run `aethis login` to mint a new one. (As of v0.6.0 the CLI prompts for sign-in inline when an authenticated command runs without a key; use `--no-prompt` to suppress that in CI.)

**`403 Forbidden: missing scope`**
Your key lacks the required scope for the command. `aethis whoami` shows what your current key can do. Contact support to upgrade scopes.

## Benchmarks

See how the engine compares to frontier LLMs on real-world eligibility rules: [aethis-examples](https://github.com/Aethis-ai/aethis-examples)

## License

MIT
