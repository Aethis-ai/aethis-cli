# Changelog

## 0.13.1 (2026-05-20)

- feat(rulesets): show the human-readable section `name` column in `aethis rulesets list` output (both the public showcase and project-scoped tables). Surfaces the new field from aethis-core v0.18.0.

## 0.13.0 (2026-05-20)

- feat: pluggable auth providers. Profiles now carry an optional `auth_mode` (default `"api_key"`) and `audience` field. The new `aethis_cli.auth_providers` module exposes a process-local registry; plugins (e.g. `aethis-cli-internal`) can `register_provider("gcloud_id_token", ...)` to add staff/internal auth schemes without touching the published package. `AethisClient` accepts an optional `auth_provider` callable, and `make_authed_client(...)` picks the right provider based on the active profile's mode.
- feat: `aethis status` now prints the active profile name + auth mode (plus audience when set). For non-`api_key` modes it shows "provider-minted at request time" instead of calling `/me`, which is X-API-Key-only.
- chore: un-hide the `--base-url` global flag in `aethis --help` (it was already implemented, just `hidden=True`).

## 0.12.3 (2026-05-19)

- fix(decide): `aethis decide --explain` no longer crashes with `AttributeError: 'str' object has no attribute 'get'`. The CLI previously treated the engine's `explanation` field as a flat `list[dict]`, but the public decide route returns a layered `{decision, decision_path?, groups: [{group, status, criteria: [{title, status, supporting_facts?, ...}]}], unused_facts}` shape. The "Rules" block now walks the actual structure and renders each group + criterion with PASS/FAIL marks, supporting fact field/value pairs underneath satisfied criteria, and a final list of unused fields (provided answers that no satisfied criterion referenced — useful for catching field-name typos).

## 0.12.2 (2026-05-19)

- fix(login): default `AETHIS_CLERK_CLIENT_ID` to the OAuth Application registered on the `clerk.aethis.ai` Clerk instance. The previous default belonged to a different Clerk app, so `aethis login` returned `invalid_client` against the dev-tools domain set in 0.12.1.
- fix(account): default `AETHIS_CLERK_DOMAIN` to `clerk.aethis.ai` for `aethis account generate` (matching the 0.12.1 change to `aethis login`); previously still pointed at the immigration domain.

## 0.12.1 (2026-05-12)

- fix: default Clerk domain changed from `clerk.aethis.legal` to `clerk.aethis.ai` so developer portal users can authenticate via `aethis login` (closes aethis-cli#40)

## 0.12.0 (2026-05-13)

- feat: `decide`, `explain`, and `fields` no longer prompt for sign-in when no API key is present. Public rulesets are now accessible with zero setup — the CLI silently uses an anonymous client and lets the server return an error only if a private ruleset is requested.
- fix: hide `--base-url` global flag from `aethis --help` (internal dev override; `AETHIS_BASE_URL` env var unchanged)
- docs: reorder `aethis --help` to lead with the no-auth explore flow, then authoring

## 0.11.1 (2026-05-10)
- fix: remove `examples/demo_core.sh` (internal dev script referencing `aethis-core` by name and a private API path — not intended for public release)
- fix: update `tests/e2e/test_spacecraft_e2e.py` to resolve the spacecraft fixture from `examples/spacecraft-crew-rules/` instead of a `tda-server` path; drop internal service name from comment
- docs: fix "rule bundle" → "ruleset" in `examples/spacecraft-crew-rules/README.md`

## 0.11.0 (2026-05-10)

- feat(updater): gh-style update-check banner. On startup the CLI
  kicks off a background thread that queries PyPI; if a newer release
  is available it prints a one-line notice to stderr at exit:
  "A new release of aethis-cli is available: 0.11.0 → 0.12.0 — to
  upgrade, run: <method-aware command>". Detects whether the install
  came via uv tool, pipx, or pip and renders the matching upgrade
  command. Result is cached for 24 h at
  `~/.config/aethis/update_check.json`. Suppressed automatically when
  stderr is not a TTY (CI, piped output). Disable with
  `AETHIS_DISABLE_UPDATE_CHECK=1`. The check never blocks the
  command — failures are silent.

## 0.10.0 (2026-05-10)

- feat(rulesets): `aethis rulesets list --public` lists the cross-tenant
  public showcase catalogue (no auth required). When run with no
  `--project-id` and no project context, falls through to the public
  catalogue automatically with a one-line hint — so a fresh signup
  sees something the moment they install the CLI instead of an empty
  list. Combine with `aethis fields -b <slug>` /
  `aethis explain -b <slug>` / `aethis decide -b <slug>` to fully
  exercise a ruleset without an API key.
- feat(profiles): named credential profiles with both per-invocation
  flag (`aethis --profile new-dev …`) and sticky default
  (`aethis profile use new-dev`). Manage with `aethis profile
  list/use/add/remove`. Reserved profile name `anonymous` forces
  unsigned mode — handy for testing what a fresh signup sees without
  losing your admin key. `aethis login --profile <name>` writes into
  the named slot. Credentials file format upgraded to
  `{active_profile, profiles: {...}}`; legacy single-key files are
  read transparently and rewritten to the new shape on next save.
- feat(client): `AethisClient(unsigned=True)` and
  `make_anonymous_client()` helper for paths that must hit the
  anonymous surface without accidentally sending a cached key.
- feat(client): `client.list_public_rulesets(limit, offset)` wrapping
  `GET /api/v1/public/rulesets`.

## 0.9.0 (2026-05-08)

- feat(publish): thread `--force` through to the server-side TDD gate
  introduced in `aethis-core` 0.11.0. `client.publish()` gains a
  `force_unsafe: bool = False` keyword; `aethis publish --force` now
  passes `force_unsafe: true` in the request body so the server-side
  gate is bypassed (and a `publish_force_bypass` audit event is
  recorded). Older engines ignore the field — no breakage. Without
  `--force`, the new gate refuses publishing over a failing test
  suite even when the CLI's own test gate is bypassed (e.g. by a
  direct curl that doesn't use the CLI). Closes the cli/server
  asymmetry that nearly shipped a 10/11 ruleset to a canonical
  `aethis/*` slug on 2026-05-07.

## 0.8.4 (2026-05-07)

- docs: link to docs.aethis.ai/agents/onboarding from MCP one-liner section

## 0.8.3 (2026-05-06)

- docs: remove Why Aethis section — package README is a reference surface (per aethis.os/positioning/surface-types.md); install / quick start / authentication is the right lead, not a problem statement

## 0.8.2 (2026-05-06)

- docs: add private-beta callout for authoring tools (decision tools remain public, no key required)
- docs: clarify in Authentication that aethis login requires an invite during the beta

## 0.8.1 (2026-05-06)

- docs: align README with positioning bible — add Why Aethis section, solution framing, TDD methodology beat
- docs: add aethis-bible: markers to derived copy blocks
- fix: replace deprecated "rule bundle" terminology with "ruleset" in pyproject.toml description

## 0.8.0 (2026-05-05)

- **Breaking**: renamed the public *bundle* concept to *ruleset* throughout the CLI to match the `aethis-core 0.10.0` API contract. The compiled rule artefact is now called a **ruleset** everywhere — in command names, in flag names, in JSON keys, and in prose. Specifically:
  - `aethis bundles list/archive` → `aethis rulesets list/archive`
  - `--bundle-id` flag → `--ruleset-id`
  - `client.list_bundles()` / `archive_bundle()` / `get_bundle_schema()` / `explain_bundle()` / `get_bundle_source()` / `set_bundle_visibility()` SDK methods → `*_ruleset`
  - JSON keys `bundle_id` / `latest_bundle_id` / `bundle_version` / `bundle_refs` → `ruleset_id` etc.
  - Default scope strings `bundles:read/explain/write` → `rulesets:*` (validated against the engine's permission registry)
- This release **requires aethis-core 0.10.0 or newer**. Older engines return `bundles:*` scopes and the CLI will reject them as invalid. Pin to `aethis-cli==0.7.2` if you need to keep working against an older engine until you can deploy.

## 0.7.2 (2026-05-03)

- Docs: replaced the stale `aethis.ai/sign-up` request-access link with `aethis.ai/developer-access` in the README "Author your own rules" section and in the `aethis whoami` hint shown when the active key has no authoring scope. After the Clerk cutover, `/sign-up` serves the Clerk SignUp form for invitees rather than the Notion request-access form, so external "Request access" pointers were broken. No code path changes.

## 0.7.1 (2026-05-01)

- Docs: README gains a dedicated **Authentication** section explaining the three modes (`aethis login` for explicit setup, lazy auth for inline mid-command sign-in, `--no-prompt` for CI). Authoring quickstart leads with `aethis init` (the v0.7.0 wizard prompts for a name and runs sign-in itself, so `aethis login` as a separate step is no longer needed). Environment-variable table expanded to cover `AETHIS_BASE_URL` and `ANTHROPIC_API_KEY`. Troubleshooting entry for `Auth error` now mentions the lazy-auth prompt and `--no-prompt`. CLAUDE.md updated to document the `aethis mcp install` path, lazy-auth helper, and `--no-prompt` flag for future agents working on the CLI. No behaviour change.

## 0.7.0 (2026-05-01)

- New: `aethis init` first-run wizard. With no args, prompts for the project name (default = current directory name); a positional `aethis init <name>` keeps working unchanged. If no API key is cached, triggers the same OAuth flow as `aethis login` *before* any filesystem writes — Ctrl-C during browser sign-in no longer leaves a half-scaffolded project on disk. After scaffolding, prints the next-step ladder (`aethis sections discover` → `fields discover` → `generate --poll`) so new users have a clear path forward. New `--no-prompt` flag for scripted use; with that flag, missing required values fail fast and missing auth surfaces a clean `AuthRequired` error instead of opening a browser. 10 new tests covering prompted, non-prompted, no-auth + interactive, no-auth + `--no-prompt`, and name-validation paths. Closes [#15](https://github.com/Aethis-ai/aethis-cli/issues/15).

## 0.6.0 (2026-05-01)

- New: lazy auth. Authenticated commands (`aethis projects list`, `generate`, `publish`, etc.) now detect missing credentials or 401 responses and offer an inline browser sign-in prompt: `"No API key. Open browser to sign in? [Y/n]"`. On accept, the same OAuth flow as `aethis login` runs, the key is cached, and the original command retries — exactly once, no infinite loops. Non-TTY stdin/stdout (CI, pipes) and the new `--no-prompt` global flag skip the prompt and surface a clean `AuthRequired` error. `--api-key <key>` still bypasses the helper entirely. New helper module `aethis_cli/auth_helpers.py`; the OAuth flow inside `commands/login_cmd.py` was factored into a reusable `run_browser_login()`. 17 new tests in `tests/test_lazy_auth.py`. Closes [#12](https://github.com/Aethis-ai/aethis-cli/issues/12).

## 0.5.0 (2026-05-01)

- New: `aethis mcp install --target <client>` writes the MCP server entry into your editor's config in one shot. Supports `claude-code` (project-level `.mcp.json`), `cursor` (`~/.cursor/mcp.json`), `claude-desktop` (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `~/.config/Claude/...` on Linux), `windsurf` (`~/.codeium/windsurf/mcp_config.json`), and `--target all` for everything at once. Idempotent, preserves any other configured MCP servers. `aethis mcp uninstall --target <client>` reverses the install. Closes [#16](https://github.com/Aethis-ai/aethis-cli/issues/16).

## 0.4.4 (2026-05-01)

- UX: `aethis login --help` now reads "Sign in and store an API key locally. First-time setup — this is all you need." `aethis account generate --help` clarifies it's for *additional* keys (rotation, multi-machine, scoped access). After successful `aethis login`, a tip line points at `aethis status` / `aethis account keys`. README quickstart collapses any "first login then generate" sequence into a single `aethis login` step. No behaviour change. Closes [#13](https://github.com/Aethis-ai/aethis-cli/issues/13).

## 0.4.3 (2026-05-01)

- Docs: README install section now leads with `uv tool install aethis-cli` (recommended) and `pipx install aethis-cli`, with `pip install` in a venv as the third option. Pairs with [Aethis-ai/docs#12](https://github.com/Aethis-ai/docs/pull/12). Closes [#14](https://github.com/Aethis-ai/aethis-cli/issues/14).

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
