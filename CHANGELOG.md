# Changelog

## 0.4.2 (2026-04-28)

Two bug fixes that block the documented quickstart against public bundles.

### Bug fixes

- **`aethis decide -b <slug>` / `explain -b <slug>` / `bundles archive -b <slug>` now accept slugs.** The classifier in `_id_utils.classify_id` previously returned `"unknown"` for slugs (e.g. `aethis/uk-fsm/universal-infant`), and `require_bundle_id` rejected them with `"is not a valid Bundle ID"`. The public API resolves both bundle IDs and slugs on `/decide`, `/schema`, and `/explain`, so the CLI now passes both through. Error message updated to mention slugs and link to `aethis bundles list`.
- **`aethis fields -b <bundle>` no longer requires an `aethis.yaml`.** It now uses the same `load_client_or_fallback()` helper as `decide`, `explain`, `bundles`, and `projects` — read-only commands work from any directory. Previously this command errored out with `"No aethis.yaml found"` even when called with a concrete bundle reference.

## 0.4.1 (2026-04-19)

### `aethis status` output polish

- Server line now shows just the URL when it's the default (`https://api.aethis.ai`) — the `(default — no override)` suffix was noise in the common case. Overrides (`AETHIS_BASE_URL`, `aethis.yaml`) still show source with a green marker.
- Identity line now says `✗ API key rejected (run \`aethis login\` to re-authenticate)` when `/me` returns 401/403/404, instead of the raw `✗ 404 from /me (Not Found)` HTTP message. Other HTTP errors keep a contextual message.

## 0.4.0 (2026-04-19)

This release ships the rich-status and read-only-from-anywhere work that the 0.2.0 notes already described but which hadn't actually been merged into a published release yet. (The code was sitting in a local branch; the prior 0.2.x/0.3.x wheels still had the minimal status command.)

### `aethis status` — context-aware summary

- `aethis status` with no args now prints CLI version, resolved server URL (with source — env / yaml / default), loaded `aethis.yaml`, bundle id from `.aethis/state.json`, and `whoami` identity (key id, tenant, tier, scopes, `can_author`). Helps answer "what will my next command actually hit?" before running it.
- `aethis status -p <project_id>` (or from inside a project dir) still shows generation progress, appended after the global summary.

### Read-only commands usable from anywhere

- `aethis explain`, `decide`, `bundles list`, `bundles archive`, `projects list`, `projects show`, `projects archive` no longer require an `aethis.yaml` in the current directory — they fall back to `AETHIS_BASE_URL` (or the default `https://api.aethis.ai`).
- `aethis explain` / `decide` now reject Project IDs (`proj_*`) passed to `-b/--bundle-id` with a one-line hint pointing at the Bundle column of `aethis projects list`, instead of silently 404'ing.

### Internals

- New `resolve_base_url_with_source()` / `load_client_or_fallback()` helpers in `aethis_cli/config.py` that the above commands share.
- New `aethis_cli/commands/_id_utils.py` + test coverage for bundle-id validation.
- New tests for `explain`, `status`, and `_id_utils`.

## 0.3.1 (2026-04-19)

### Docs cleanup

- README and [docs.aethis.ai/interfaces/cli](https://docs.aethis.ai/interfaces/cli) no longer document `AETHIS_BASE_URL` or show `base_url:` in the `aethis.yaml` example — public users always hit `https://api.aethis.ai`, and the documented values were just duplicating the default. The env var still works as an override for devs and CI; it's intentionally undocumented.
- Dropped the `AETHIS_CLERK_DOMAIN` env var from the README (marked "development only" and confusing for public users). The override still works in code.

## 0.3.0 (2026-04-19)

### Trim public CLI to the developer API surface

The public CLI now only ships commands every developer can use against `https://api.aethis.ai`. Privileged and staff-only commands have been removed and will live in a separate internal plugin package.

**Breaking changes:**

- **Removed `aethis source`** — internal-only DSL viewer; moved to the `aethis-cli-internal` plugin.
- **Removed `aethis account permissions`** — IAM permission registry; internal-only.
- **Removed the `aethis guidance domain …` group** (and the deprecated `aethis domain guidance …` alias) — domain-level guidance is staff-managed.
- **Removed the global `--base-url` flag** (plus the per-command `--base-url` on `login`, `account generate`, `account keys`, `account revoke`). The `AETHIS_BASE_URL` env var still overrides the default. The flag had no meaning for the public API target and cluttered `--help`.

**New: third-party plugin support.**

- The CLI now discovers plugins via Python entry points under the `aethis_cli.plugins` group. A plugin exposes one callable `register(app: typer.Typer) -> None` and attaches extra commands to the root app. Plugin load failures print a single warning to stderr and never crash the CLI.
- The staff-facing `aethis-cli-internal` package uses this hook to re-attach `source`, `domain guidance`, `permissions`, and the `--base-url` flag.

## 0.2.1 (2026-04-19)

### Consolidated guidance command tree

- `aethis domain guidance ...` moved under `aethis guidance domain ...` — the `domain` group exists only to host `guidance`, so having two top-level trees for the same concept was confusing. All four subcommands (`add`, `list`, `import`, `export`) behave identically on the new path.
- The old `aethis domain guidance ...` path still works as a hidden deprecated alias: invocations continue to succeed and emit a one-line deprecation notice to stderr. It is no longer shown in `aethis --help`. Planned removal in a future release.

## 0.2.0 (2026-04-19)

### `aethis status` — global CLI context

- **New behaviour**: `aethis status` (no args) now prints a one-screen summary of the current CLI context: CLI version, resolved server URL (with source — `--base-url` / env / yaml / default), loaded `aethis.yaml` + project, bundle id from `.aethis/state.json`, and whoami identity (key id, tenant, tier, scopes, `can_author`). Answers "what will the next command hit?" — the usual cause of "why is my project missing?" is talking to the wrong server.
- **Backward compatible**: `aethis status -p <project_id>` (or invoked from a project dir) still shows generation progress, now appended after the global summary.

### UX improvements for read-only commands

- `aethis explain`, `decide`, `bundles list`, `bundles archive`, `projects list`, `projects show`, and `projects archive` no longer require an `aethis.yaml` in the current directory — they fall back to `AETHIS_BASE_URL` (or the default `https://api.aethis.ai`) when invoked from anywhere.
- `aethis explain` and `decide` now reject Project IDs (`proj_*`) passed to `-b/--bundle-id` with a one-line hint pointing at the `Bundle` column of `aethis projects list`, instead of silently proceeding to a 404.
- `aethis --base-url <url>` is now a top-level flag, equivalent to setting `AETHIS_BASE_URL` for one invocation. Lets you hit staging or a self-hosted instance without editing `aethis.yaml`.
- `aethis projects list` prints a short tip after the table showing how to copy a Bundle value into `aethis explain -b …`.
- Configuration and authentication errors now render as a single red line via the existing `cli()` handler, not a Rich traceback panel. `pretty_exceptions_enable=False` is set on every Typer app.

### Better `--help`

- Top-level `aethis --help` now shows common flows (status, list, explain, decide), authoring flow, and how to target a different server.
- `explain`, `decide`, `bundles list`, `projects list`, and `status` all have "Examples:" blocks in their per-command help.

## 0.1.0 (2026-04-05)

Initial release.

### Features

- **Account management**: `aethis account generate` (browser OAuth), `aethis account keys`, `aethis account revoke`
- **Project authoring**: `aethis init`, `aethis generate --poll`, `aethis test`, `aethis publish`
- **Decision tools**: `aethis decide`, `aethis fields`, `aethis explain`
- **Project management**: `aethis projects list`, `aethis bundles list`, `aethis bundles archive`
- **Security**: HTTPS enforcement, OS keychain storage, PKCE OAuth flow
- **Example**: Spacecraft Crew Certification Act 2049 with 5 golden test cases
