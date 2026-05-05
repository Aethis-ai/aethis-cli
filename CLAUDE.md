# CLAUDE.md

Agent notes for `aethis-cli`. Human-facing docs live in [README.md](README.md) and on [docs.aethis.ai](https://docs.aethis.ai).

## What this is

The public developer CLI for the Aethis platform — `aethis decide`, `aethis fields`, `aethis explain` for decisions (no auth needed), and `aethis generate` / `aethis test` / `aethis publish` for authoring (API key needed). Published to PyPI as `aethis-cli`.

## Dev loop

```bash
uv sync
uv run aethis --help

# Run against a local aethis-core for integration work:
export AETHIS_BASE_URL=http://localhost:8080
export AETHIS_API_KEY=test
uv run aethis status

# Against prod (default), use a real key:
uv run aethis login              # browser OAuth → stores ak_live_... at ~/.config/aethis/credentials
```

## Command taxonomy

Decision group (no key needed against public rulesets):

- `aethis decide -b <ruleset|slug> -i '{...}'`
- `aethis fields -b <ruleset|slug>`
- `aethis explain -b <ruleset|slug>`

Project / authoring group (requires `projects:write`):

- `aethis init` (or `aethis init <name>`) — first-run wizard: prompts for a name, runs `aethis login` if not authed, scaffolds `.aethis/` + `aethis.yaml`. As of v0.7.0 this runs auth *before* any filesystem writes so a Ctrl-C during the browser flow doesn't leave a half-scaffolded project.
- `aethis sections discover --file <path>` — phase 1
- `aethis fields discover --section <id>` / `aethis fields set` — phase 2
- `aethis generate --poll` + `aethis test` + `aethis refine --hint ...` — phase 3 TDD loop
- `aethis publish [--slug my-team/my-ruleset]`

Account:

- `aethis login` / `aethis logout` / `aethis status` — `login` is the canonical first-time setup; mints + caches the key in one step.
- `aethis account generate` — mint an *additional* key (rotation, multi-machine, scoped access). For first-time setup use `aethis login`.
- `aethis account keys` / `aethis account revoke <key_id>`

MCP wiring (added v0.5.0):

- `aethis mcp install --target <claude-code|cursor|claude-desktop|windsurf|all>` — writes the MCP server entry into the user's editor config. Idempotent, preserves any other configured servers.
- `aethis mcp uninstall --target <client>` — reverses the install (removes only the `aethis` entry).
- This is the documented install path; the [aethis-mcp](../aethis-mcp/) README's "Manual install" tabs are the fallback for users without `aethis-cli`.

Global flags:

- `--api-key <key>` — overrides the cached credential and the lazy-auth helper (one-shot).
- `--no-prompt` — suppresses lazy-auth's "Open browser to sign in?" prompt (CI / scripts). Combined with no cached key, authenticated commands fail fast with a clean `AuthRequired` error.
- `AETHIS_BASE_URL` env var — staff/dev override for the API host (staging or self-hosted). Not exposed as a CLI flag in the public build.

## Architecture

- [aethis_cli/commands/](aethis_cli/commands/) — one file per command group, Typer-based
- [aethis_cli/state.py](aethis_cli/state.py) — reads/writes `.aethis/state.json` to keep the current `project_id`, last generation job, and published slug between invocations
- All HTTP calls go through [aethis_cli/api.py](aethis_cli/api.py) against the configured `AETHIS_BASE_URL`. No direct Mongo access.

## Gotchas

- **`.aethis/` is the stateful directory.** `aethis.yaml` is the user-edited config; `state.json` is the tool-managed cache of IDs. Don't hand-edit `state.json`.
- **Version bump rule.** Published package — bump `_version.py` + `pyproject.toml` + CHANGELOG on every change. See `../.claude/rules/public-repos.md`.
- **Decision endpoints return `undetermined`, not an error, when fields are missing.** The shell exit code is still 0; parse the JSON decision field rather than relying on the exit code for eligibility-vs-incomplete distinctions.
- **Slug namespace `aethis/*` is reserved.** External tenants will get HTTP 403 with `reason_code: reserved_namespace` if they try `--slug aethis/foo`. Internal use only.
- **`aethis publish` runs tests server-side as a gate.** A green local `aethis test` can still be rejected at publish if the last generation's tests haven't been re-run after edits.
- **Lazy auth is the default** (since v0.6.0). Authenticated commands without a cached key prompt the user inline ("Open browser to sign in? [Y/n]") and retry exactly once on success. The OAuth flow lives in [aethis_cli/commands/login_cmd.py](aethis_cli/commands/login_cmd.py) (`run_browser_login()`); the lazy-auth glue is in [aethis_cli/auth_helpers.py](aethis_cli/auth_helpers.py). When extending, reuse `require_auth_or_login_inline()` instead of re-implementing the prompt; honour `--no-prompt` and the `AethisClient.on_auth_required` hook so 401-refresh-and-retry stays consistent.

## See also

- Workspace operational index: [../docs/OPERATIONAL_INDEX.md](../docs/OPERATIONAL_INDEX.md)
- Public docs: [docs.aethis.ai](https://docs.aethis.ai)
- Recipes for the three standard flows: [docs.aethis.ai/recipes](https://docs.aethis.ai/recipes/evaluate-a-case)
