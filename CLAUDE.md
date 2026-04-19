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

Decision group (no key needed against public bundles):

- `aethis decide -b <bundle|slug> -i '{...}'`
- `aethis fields -b <bundle|slug>`
- `aethis explain -b <bundle|slug>`

Project / authoring group (requires `projects:write`):

- `aethis init <name>` — scaffold `.aethis/` + `aethis.yaml`
- `aethis sections discover --file <path>` — phase 1
- `aethis fields discover --section <id>` / `aethis fields set` — phase 2
- `aethis generate --poll` + `aethis test` + `aethis refine --hint ...` — phase 3 TDD loop
- `aethis publish [--slug my-team/my-bundle]`

Account:

- `aethis login` / `aethis logout` / `aethis status`
- `aethis account generate` — mint a new key via Clerk

Global flags: `--base-url` and `--api-key` override the environment variables.

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

## See also

- Workspace operational index: [../docs/OPERATIONAL_INDEX.md](../docs/OPERATIONAL_INDEX.md)
- Public docs: [docs.aethis.ai](https://docs.aethis.ai)
- Recipes for the three standard flows: [docs.aethis.ai/recipes](https://docs.aethis.ai/recipes/evaluate-a-case)
