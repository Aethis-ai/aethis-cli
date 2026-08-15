# Changelog

## Unreleased

## 0.34.0 (2026-08-15)

Generating a ruleset no longer has to activate it.

- **feat: `aethis generate --no-publish` leaves the ruleset an unpublished draft.** A successful `aethis generate --poll` publishes what it produced, and publishing *activates* it. For authoring that must leave a draft behind — a ruleset that only ever activates somewhere else, after promotion — there was no way to opt out, and the only workaround was to archive it immediately afterwards, which leaves a real window where it is live. The flag suppresses the publish and nothing else: the poll, its timeout, and the post-generation field diff are untouched, because those are what make the run worth doing.
- **feat: the ending says which one it was.** A run that was told not to publish reads differently from one whose publish failed. Both leave a draft and both point at `aethis publish`, so collapsing them would tell an author who never passed the flag that everything went to plan — precisely when they most need to know it did not. The deliberate ending names the flag; the failure ending is unchanged.
- **Unchanged without the flag.** It defaults off, and `aethis refine`, which shares the same machinery, was not asked to change and does not: the publish still happens on the same call with the same argument.

## 0.33.0 (2026-08-15)

A pinned enum's members are checked, not just its name — and the check survives
the failure it was written to explain.

- **fix: a failed generation now prints the drift report before it exits.** The diff existed for the case where the model did not honour the pin, and that was the one case it could not reach: the poll loop exited the moment a job reported `failed`, several frames below the call that would have printed it. A failed generation said "Generation failed" and nothing about what had actually been produced. The poll now reports how the run ended and the caller decides, so the diagnostic runs first and the exit code is unchanged.
- **fix: the diff is computed against the ruleset *this* run produced — never the last one recorded.** It previously read the ruleset id off `.aethis/state.json`, which is written only on success, so a run that produced nothing would have been diffed against an earlier generation and the result would have read exactly like this one's. Where a run names no artefact — which is every failure today, since the engine records one only on its success paths — the CLI says so plainly instead of falling back on anything.
- **fix: the ruleset a successful generation reports is the one *that job* produced, not the project's newest.** The id was read from `latest_ruleset_id`, which is a property of the project — so with two generations running against one project it could name another run's artefact, both in the diff and in the id written to disk for every later command to default to. The job's own `result_ruleset_id` is now preferred, with the project-level value kept only as a fallback for engines that record nothing on the job.
- **fix: a generation whose polling breaks off no longer goes quiet about the recorded ruleset.** An API error mid-poll — a 500, a dropped connection — exits without a verdict, and the job may well have succeeded regardless. Like a timeout, nothing has been ruled, so the recorded id stands; unlike before, it is named, because that ending was otherwise the remaining way to reach a silent `aethis fields pull` from an earlier generation.
- **fix: a failed generation no longer leaves a ruleset pointer that reads like its result.** The recorded id is what `aethis fields pull` defaults to, so after a failure the next pull synced from the *previous* generation without a word, and the fields appeared to be the ones just generated. A failure now clears it — naming it, so it can still be passed with `--ruleset-id` — and `fields pull` refuses rather than guessing. A timeout is treated differently on purpose: nothing has been ruled, the job may still land, so the pointer stands and is named as stale instead of discarded.
- **fix: an enum that came back with *no* members is reported as drift, not success.** The member comparison skipped a produced enum whose member set was empty, reading it as "nothing to compare" — so a field whose members had **all** been dropped printed `✓ Fields: all N pinned field(s) were produced`. That is the worst case the report exists to catch, reported as the best one. The empty set is now the loudest result, and a field that came back as something other than an enum reads the same way.
- **fix: a schema that cannot be read is said out loud.** The fetch failure was swallowed entirely, so a purged or unreachable draft produced a run with no verdict at all — indistinguishable from a clean one. It is now reported, naming the ruleset, and still never fails the command.
- **fix: `aethis generate` compares pinned enum *member sets*, not only field keys.** The post-generation drift report asked whether each pinned field was produced. It was — so a schema whose enum had quietly grown a member nobody pinned printed `✓ Fields: all N pinned field(s) were produced`. The members were already in hand locally; only the comparison was lossy. It now checks each pinned enum by exact equality in both directions and names the added or dropped members.
- **Why it matters beyond tidiness.** A field can be present, correctly typed, and still wrong. Where an enum is used as an escape hatch — a value that keeps a decision at *undetermined* pending human review — the safety property is that no value the schema allows can turn that into a definitive "no". One unpinned member breaks it, and no test case can catch that: a test occupying the offending value would itself be the bug. Five consecutive generations of a real ruleset were scored as clean this way.

## 0.32.0 (2026-08-05)

Re-uploading a test suite no longer leaves a second copy of it.

- **fix: `aethis generate` replaces the project's test cases instead of adding another copy of them.** `tests/scenarios.yaml` is the authoritative suite and it was uploaded in full on every run, while the API only ever appended — so N authoring cycles left N copies of every case. Nothing errored: duplicates inflate the denominator of every pass rate, so a run reported a total that looked like a result and was partly copies of itself. Two real projects were found holding 185 and 96 cases against files of 42 and 14. The upload now asks the engine to replace, which makes it idempotent.
- **feat: the count of cases the upload overwrote is reported.** Replacing is destructive — it removes what was on the project — so `aethis generate` prints how many cases went and how many arrived (`Uploaded 42 test case(s) from scenarios.yaml — 42 replaced`) rather than performing it silently.
- **feat: an engine that cannot replace is named, not worked around quietly.** Replacing needs an engine that offers it, and one that does not offer it does not reject the request — it ignores the member and appends, so sending it blindly would restore the duplication with nothing to notice. The CLI reads the engine's own published schema, sends the flag only where it is advertised, and where it is not — or where the schema could not be read at all, which is a different answer and says so — prints that the upload APPENDED, that running again adds another copy, and what to do about it. Nothing is dropped silently in either direction.

## 0.31.0 (2026-07-27)

Publish with citations — and be able to cite a document you hold, not just one
the internet happens to host.

- **feat: `aethis publish --source-targets <file>` resolves a ruleset's citation keys.** A YAML or JSON targets file maps each citation key your criteria declare (`source_refs`) to the document it cites: title, authority, licence, and the **verbatim** quoted text. The engine verifies every quote against the source bytes at publish time and rejects the publish if any citation fails — there is no half-cited ruleset.
- **feat: a citation can point at a file you uploaded, not only a public URL.** An entry naming `file:` is uploaded to the project and cited by its `source_id`, so the rules can cite the very documents they were generated from. The engine resolves it from retained bytes with no network call at all.
- **feat: an identical file is never uploaded twice.** File targets are matched by `sha256` against the project's existing sources and reused when the bytes are already there — including two entries in the same run naming byte-identical files. The API does not deduplicate uploads, so re-running a publish previously grew the project a duplicate source per citation.
- **feat: a malformed targets file costs no round trip.** Exactly-one-of `url`/`file`, a readable file, an `https://` URL, the required `title`/`authority`/`licence`/`quote.exact`, and unknown fields are all checked locally — every problem in the file reported at once, before the first API call, so nothing is uploaded against a targets file that was never going to publish.
- **feat: an uploaded-artefact citation is never rendered as though it were a public link.** `aethis decide --explain` and `aethis explain` label these references as an uploaded snapshot verified at publish, state that the download is authenticated and needs a key with `projects:read` on the project, and resolve the engine-relative download path against the host you called — while a URL citation keeps reading as the public link it is. `aethis publish` reports the same distinction per target as it resolves them.
- **feat: `aethis` can list a project's uploaded sources** (`AethisClient.list_sources`), which is what makes the digest comparison above possible.
- **fix: a rejected citation now says which one and why.** Publish-time citation resolution is fail-closed and the API itemises every failing key (`{source_id, reason_code, message}`), but the CLI collapsed the whole envelope to its summary line — an author with three citations and one wrong quote learned neither which key failed nor what was wrong with it. Every itemised failure is now printed under the error. Applies to any endpoint returning a `failures` list, not just publish.
- **feat: citation targets that never landed are reported, not silently dropped.** The engine only resolves the citation keys the compiled ruleset actually declares, and ignores the rest — so a mistyped key published "successfully" with zero citations attached, after uploading the files. After a publish with `--source-targets`, the CLI reads the published ruleset back and says how many targets landed, naming any that did not. A failed read-back degrades to an honest note; it never turns a successful publish into a failure.
- **feat: a failed publish says your uploads are still there.** Publishing is fail-closed but the uploads that preceded it are not rolled back, and re-running reuses them by digest rather than duplicating them. The error now says so, instead of leaving the state of the project a guess.
- **fix: a duplicate citation key is rejected instead of silently overwriting.** YAML and JSON both let the last definition of a repeated key win; in a citation manifest that quietly discards a document and publishes the other one into an immutable ruleset. Both formats now refuse duplicate keys (at any depth in the file).

- **ci(publish): the downstream-unstick sweep now reaches every consumer repo.** The `unstick-downstream` job searched a single owner, so a PR carrying an `aethis-needs: aethis-cli` marker in a repo under a different owner was never found and sat as a draft indefinitely after the release it was waiting for went out. It now queries each consumer repo individually with `gh pr list --repo`, which is genuinely repo-scoped, and matches the marker against the PR body returned by that same call (one request per repo instead of a search plus a fetch per hit). A repo the token cannot read is reported as a warning instead of failing the whole sweep. No package or runtime change.

## 0.30.0 (2026-07-26)

Safety and provenance for everything the CLI reads back from the API.

**Versioning note.** This release changes two documented behaviours — `aethis
explain --output json` emits the whole envelope rather than the bare
`criteria` array, and a blocked evaluation now exits `3` where it previously
exited `0`. Under strict SemVer a breaking change is a major bump; taken as a
**minor** here because the package is pre-1.0 (`0.x`), where the published
rule is that minor carries breaking changes. Recording the call explicitly
rather than leaving it to be inferred: both changes replace behaviour that was
unsafe (a script could not tell a rejected input from a decision), which is
why they ship rather than waiting for 1.0.

- **feat: a rejected input can never look like a result.** When a decision response carries blocking `field_errors`, `aethis decide` **and `aethis rulebooks decide`** print the rejected inputs instead of a verdict and exit `3` (new exit code: `0` decided, `1` call failed, `3` inputs rejected). JSON output reports `"decision": "undetermined"` and records the block under `aethis_cli_contract`. The CLI enforces this rather than trusting it: if a server ever returns `eligible` beside blocking errors — a stale deployment, a caching proxy, a third-party API-compatible server — the contradiction is overridden, reported, and never rendered as success in human output, in JSON, or through the exit status. New `aethis_cli.contract` module owns the rule.
- **feat: immutable identity on every decision surface.** Human output gains a `Ruleset identity` block (ruleset id, published version, `sha256:` content digest, engine version, decision id, inputs hash) so a decision can be reproduced or audited later. A `ruleset_version` of `unknown` — which a published ruleset must never report — is called out as unresolved instead of printed as though it were an identity.
- **feat: supporting sources are shown, and kept separate from the rules.** Where a ruleset publishes validated source references, `aethis decide --explain` and `aethis explain` render them under their own `Sources` heading: document title, authority, locator, the verbatim quoted text, deep link, licence, verification time and source digest. A reference that arrived incomplete is flagged rather than rendered as a confident-looking citation. When a ruleset publishes none, the output says so rather than showing nothing.
- **feat: output distinguishes result, logic trace and source.** The three now have their own headings, and the logic trace is labelled as explanatory — per-criterion statuses answer "what is true of this criterion", never "what may I act on".
- **change: `aethis explain --output json` now emits the whole envelope** (`ruleset_id`, `slug`, `ruleset_version`, `content_digest`, `criteria`) instead of the bare `criteria` array. Provenance on the machine-readable path is the whole point of the identity contract; a script that consumed the old shape needs `.criteria`.
- **feat: undeclared `/decide` request options are refused locally.** The API rejects an unknown top-level request member with a 422 rather than ignoring it; the CLI now names the offending option before spending a round-trip, and renders the server's validation envelope readably if one is ever returned.
- **feat: the capability boundary is visible before you hit it.** Root help, `decide`/`explain` help, the README and every auth-required error now state plainly that evaluation needs no account and no key, and that authoring is invite-only (with the access link).
- **feat: release integrity and hermetic first-install evidence.** `scripts/release-integrity.py` binds the exact published bytes to the commit they were built from (version + sdist/wheel `sha256` + source commit) and can re-check that against the files the registry serves. `scripts/hermetic-install-check.py` proves the CLI works for someone who has never run it: temporary HOME/XDG/config/cache, no Aethis or provider credentials, empty-cache install from one source only, across supported runtime/OS/architecture — with a poisoned-cache negative control that must fail. Both run in CI on every PR (Linux + macOS, Python 3.11/3.12/3.13) and on every release, before and after publication.
- **feat: the guard matches the engine's own forcing sweep exactly.** When a response is blocked, all five embedded copies of a terminal verdict are scrubbed — top-level `decision`, `explanation.decision`, `explanation.decision_path`, `trace.status`, `trace.path` — so a blocked result can never be printed above a green "Satisfied by: …". A `--json <fields>` projection can no longer drop the `aethis_cli_contract` record either.
- **fix: a non-JSON response body is an error, not a traceback.** A 2xx carrying HTML (an intermediary's error page, a truncated body) now surfaces as one readable API error instead of a `JSONDecodeError` escaping mid-command.
- **fix: documented commands that would not run.** `--output` is a root option and must precede the subcommand; five documented invocations had it after (including the README's flagship shell-gate example, whose `else` branch therefore misreported). All fixed, and a test now resolves every documented invocation against the real command tree so this class cannot come back.
- **test: the contract oracle is itself gated.** `scripts/mutation-check.py` breaks the contract sixteen different ways — deleting each scrub site, redefining the blocking exit code, making the blocking predicate always-false, unguarding the rulebook surface — and requires the suite to go red for every one. It runs in CI.
- **test: contract fixtures are captured, never hand-written.** The new tests run against payloads recorded from a live engine (terminal decisions, each class of blocking input error, an incomplete evaluation, the 422 for an undeclared request member, the explain envelope) plus source-reference DTOs serialised by the engine's own model. Regenerate with `scripts/gen-contract-fixtures.py`; provenance is documented in `tests/fixtures/contract/README.md`.

- **fix: no spurious traceback when a login callback is cancelled or times out.** The OAuth callback server closed its socket while its background thread was still waiting on it, so the thread died on `ValueError: Invalid file descriptor: -1` and printed an unhandled-exception traceback after `aethis login` timed out. The serving thread is now signalled before the socket closes, and treats a closed socket as its exit condition rather than an error.

## 0.29.0 (2026-07-21)

- **feat: `aethis usage`** — show your rate-limit budget per operation class (decide / generate / author / read / keys / admin) as a table: used / limit / remaining / reset, over the rolling 24h window. `generate` (LLM rule generation) is the scarce class; browsing and status polling (`read`) are effectively unlimited. `--json`/piped emits the raw `/usage` payload. New `AethisClient.usage()`.
- **feat: remaining generate budget after `aethis generate`.** The CLI now reads the `X-RateLimit-*` response headers (captured on every request as `AethisClient.last_rate_limit`) and, after a generation is queued, prints "N generations left in the current 24h window" — so a 429 is never the first signal. The line turns yellow at ≤5 remaining.
- Requires aethis-core with the `GET /api/v1/public/usage` endpoint + `X-RateLimit-*` headers live (epic aethis-workspace#552, P2). The public release of this version is held until that surface is live on `api.aethis.ai`.

## 0.28.0 (2026-07-20)

- **feat: "what's new" on `aethis update`.** `aethis update` / `aethis update --check` now shows the changelog entries between your installed version and the latest release (titles + notes, newest ≤5, long notes truncated), sourced from the project's GitHub Releases. If the Releases API is unreachable, rate-limited, or has nothing in range, it falls back to a link to the Releases page — the command never errors or hangs on this. The exit-time update banner also gained a "what's new →" link to the same page. Coverage is forward-fill: only releases cut from here on populate the range, so an old install may see a gap. New `update_check._fetch_github_releases()`; the display logic lives in `update_cmd._releases_in_range()` / `_print_whats_new()`. (epic aethis-workspace#537)
- **ci: cut a GitHub Release on publish.** The `publish` workflow now creates a GitHub Release for each just-published tag, using that version's `CHANGELOG.md` section as the release notes, so the "watch → releases" subscribe channel stays current automatically. Idempotent (create-or-skip on an existing Release) and `--verify-tag` (never mints a synthetic tag). No package/runtime change. (epic aethis-workspace#526)

## 0.27.0 (2026-07-19)

- **feat: `aethis review [<project>]`** — the Authoring Coach report for a project. Runs the server-side rubric and prints an authoring score, 2–3 evidence-cited strengths, and the single highest-leverage next improvement (with its docs link and the lever that fixes it). Defaults to the current project in `.aethis/state.json`; pass a `proj_…` id to review any of your projects from anywhere. `--verbose` shows the full per-check table; `--json` (and any piped/`--output json` invocation) emits the raw `ReviewReport`. The deterministic report needs only your API key; `--coach` opts into LLM mentoring prose billed to your own Anthropic key (`ANTHROPIC_API_KEY`). Advisory only — the exit code is always 0 regardless of score. New `AethisClient.review()`.
- **feat: every request now sends `X-Aethis-Client: cli/<version>`** so the server can attribute per-surface telemetry (CLI vs MCP). The header carries no credentials and no PII, and is set once at client construction for all commands.
- Requires aethis-core with the `/api/v1/public/projects/{id}/review` endpoint live (epic aethis-workspace#514, P1). The public release of this version is held until that endpoint is live on `api.aethis.ai`.

## 0.26.0 (2026-07-17)

- **feat(rulebooks): `aethis rulebooks graph <id>`** — fetch and render the rulebook-level ruleset-map dependency graph (field -> criterion -> group -> outcome). Prints a node-count summary + a table of nodes (id, type, the criterion's human-readable `display.sentence`, field count); `--mermaid` prints the raw Mermaid diagram source for piping into a renderer; `--output json` returns the full payload (`{rulebook_id, graph: {nodes, edges, sections, stats}, mermaid}`), including each node's `display.routes`/`display.expr` for programmatic consumers. This endpoint requires a valid API key even for a public rulebook (confirmed against the live engine) — unlike the ruleset-level graph below, there's no anonymous path. New `AethisClient.get_rulebook_graph()`.
- **feat(rulesets): `aethis rulesets graph <ruleset_id>`** — the single-ruleset analogue, open for public rulesets with no API key required (`load_client_or_anon`). Same table/`--mermaid`/`--output json` shape. New `AethisClient.get_ruleset_graph()`.
- **feat: `--include-graph-overlay` on `aethis decide` and `aethis rulebooks decide`** — stamps the decision's per-criterion status onto the rule-map graph, returned as a `graph_overlay` field on the response (`--output json` to inspect it). Additive request flag; a plain-text hint is printed when the overlay is present and JSON wasn't explicitly requested.
- **feat(rulebooks): `aethis rulebooks schema` surfaces `engine_version`.** The schema response already carries the aethis-core build that served it (e.g. `aethis-core@0.45.2`); the CLI now prints it as a header line ahead of the schema payload instead of leaving it buried in the JSON.
- Requires aethis-core 0.40.0+ (live on `api.aethis.ai` / `staging.api.aethis.ai` as of this release) for `/graph`, `include_graph_overlay`, and `engine_version` on `/schema`. `robot_hints` (shipped v0.23.0) is unaffected by this release.

## 0.25.0 (2026-07-15)

- **feat: authorization errors now render the server's `hint` and the missing scope readably.** A `403 denied_missing_permission` (and `401`) previously printed the raw error object on commands that render their own errors (`projects`, `whoami`, …); the CLI now renders one clean line naming the missing permission plus, on its own dim line, the server's follow-up hint (e.g. how to request access). The top-level handler and the per-command renderer now share one formatter (`aethis_cli.output.format_error_detail` / `render_api_error`), so every command surfaces the same readable message. The hint is rendered with markup disabled (so a hint containing `[brackets]` isn't dropped) and non-string `missing_permissions` items are coerced (so a server quirk can't turn the error into a traceback).
- **test: new staging integration lane (`tests/integration/`, marker `staging`).** Acquires an API key the self-serve way (a fenced e2e user's session → mint with the server's default scopes, no `scopes` field), drives the CLI core loop against deployed staging (`whoami`/`status`, `projects list`/`archive`, `rulesets`/`explain`/`fields`/`decide` against a public showcase ruleset), and asserts the negative paths a caller actually sees — a scope-reduced key's 403 and a revoked key's 401 — with the error envelopes checked against the machine-readable public-API contract. Report-only nightly workflow (`staging-integration.yml`); never gates a merge. Run locally with the one-liner in `tests/integration/README.md`.
- **test: the spacecraft authoring e2e moved to its own weekly lane** (`authoring-e2e-weekly.yml`). It drives the LLM authoring pipeline, so it is kept out of the nightly LLM-free cadence; the model is passed explicitly via `X-Anthropic-Key`, generation is bounded by an explicit iteration cap (`SPACECRAFT_GENERATION_TIMEOUT`), and the `manual` marker stays as the local escape hatch.

## 0.24.0 (2026-07-04)

- **fix: network errors now render one actionable line, not a raw traceback.** When the API is unreachable, times out, or a DNS/TLS error occurs, every command now prints `Could not reach the Aethis API at <url>: <reason>.` plus a "check your connection" hint and exits non-zero, instead of dumping an `httpx` stack trace. The top-level handler catches `httpx.HTTPError` (the umbrella over connect/timeout `RequestError`s), matching the graceful handling `login`/`account` already had.
- **feat: non-interactive environments bypass confirmation prompts.** A truthy `AETHIS_NONINTERACTIVE` or `CI` env var (values `1`/`true`/`yes`, case-insensitive) now flips the whole process non-interactive, so destructive commands (`account revoke`, `rulesets archive`, `projects archive`, `rulebooks archive`, `rulebooks tests delete`) proceed without waiting on stdin, so a background job or CI step no longer hangs on a `[y/N]` prompt. The bypass prints a one-line notice so it's never silently active. The explicit per-command `--yes`/`-y` flags keep working unchanged. New shared `aethis_cli.prompts.confirm_or_abort` helper.
- **docs: refreshed the worked examples in `decide`/`explain`/`fields` help** to use public showcase rulesets (`aethis/spacecraft-crew-certification`, `aethis/consumer-credit-prequalification`) instead of product-specific slugs.
- **chore: `make install` uses `uv pip install -e ".[dev]"`** (matching the README) instead of bare `pip`.
- **minor:** dropped the unused upgrade-command strings from `update_check._detect_install_method` (it now returns just the detected method; the concrete upgrade argv is still built by `update`'s `_upgrade_argv`); `decide` reads the decision field with a safe default so a payload without `decision` renders as `unknown` rather than raising `KeyError`.

## 0.23.0 (2026-06-25)

- **feat(rulebooks): declare `robot_hints:` in a rulebook file and push them to the engine.** Rulebook authors can now provide natural-language guidance for the conversational assistant alongside the rulebook's other configuration.
  - **`aethis rulebooks create <name> --file rulebook.yaml`** — a new `--file`/`-f` option reads a `robot_hints:` block (a sibling of `name`/`domain`/`outcome_logic`) from a `rulebook.yaml`/`.json` and sends it on create. CLI flags still own `name`/`domain`/`slug`/`description`; only the hints are taken from the file. No `--file` (or a file without a `robot_hints:` key) is a clean no-op — behaviour is unchanged.
  - **`aethis rulebooks set-logic <id> -f rulebook.yaml`** now also accepts a *wrapped* form: when the top-level object carries an `outcome_logic:` key, a sibling `robot_hints:` block is pushed in the same update. A bare Expr AST file (the prior shape) is still accepted unchanged.
  - `robot_hints` is a mapping of beat-name to a natural-language string. Active beats: `general_context`, `preamble`, `session_start`, `postamble`, `session_end`, `stuck`. Reserved beats (accepted, not yet acted on): `persona`, `conversational_style`, `section_transition`. Unknown beat keys and non-string values are rejected client-side with a clear message before the round-trip.
  - New optional `robot_hints` parameter on `AethisClient.create_rulebook()` / `update_rulebook()`; omitted from the request body when not supplied, so calls against an older engine are unaffected.
  - Requires aethis-core with the rulebook `robot_hints` field (aethis-core#220); mid-deploy to staging at time of writing. Against an engine without it, the field is ignored/rejected server-side.

## 0.22.0 (2026-06-16)

- **feat(fields): `aethis fields` is now a command group for the full field-authoring loop.** Bare `aethis fields [-b <ruleset>]` still shows a ruleset's field schema (unchanged); three subcommands manage the local `fields/fields.yaml`:
  - **`aethis fields discover`** — uploads the project's `sources/` (creating the project if needed), runs server-side LLM field discovery, and merges the proposals into `fields/fields.yaml` so you start from a real draft instead of a blank file. Existing entries are preserved — only new keys are appended — so hand-authored labels/questions/hints are never clobbered. Prints the completeness score and any critical gaps. Needs an LLM key (`ANTHROPIC_API_KEY`), same as `generate`; without one it fails with a clear message naming the env var instead of a raw server header error. New `AethisClient.discover_fields()`.
  - **`aethis fields pull`** — syncs the server's authoritative produced fields (key + type + enum values) back into `fields/fields.yaml` so local matches reality after a generate. Local-only `label`/`hints` are preserved; fields absent from the server schema are kept and reported rather than silently dropped.
  - **`aethis fields validate`** — checks `fields/fields.yaml` before upload: valid `type` (int/bool/string/enum/date/duration), no duplicate keys, `enum` requires `enum_values`. The same validation now also runs inside `aethis generate`, per contributing file (rulebook + ruleset), so duplicate keys within a file fail fast before any server state changes.
  - `discover`/`pull` only ever write a vocabulary that re-validates: an unknown server type or an `enum` with no values falls back to `string` instead of producing a file the next `validate`/`generate` would reject. Writes also preserve any hand-authored keys the tool doesn't model (e.g. `description`) rather than dropping them.
- **feat(generate): the field spec/produced diff is surfaced after generation.** After a successful `aethis generate`, the CLI compares the pinned field vocabulary against what the engine actually produced and prints pinned-but-not-produced / produced-but-not-pinned fields (with a pointer to `aethis fields pull`) instead of the drift passing silently.
- **feat(init): rulesets can declare rulebook membership explicitly.** A `rulebook:` key in a ruleset's `aethis.yaml` (a path to the enclosing rulebook) now declares membership directly; the directory-position convention (`<rulebook>/rulesets/<ruleset>/`) remains the fallback. The `init` scaffold documents the key.
- **perf: source uploads are now idempotent.** `discover` and `generate` share one project-resolution + upload path, and a per-file mtime ledger in `.aethis/state.json` means a `discover` followed by a `generate` (or repeated generates) only re-uploads sources that actually changed instead of re-pushing the whole `sources/` tree each time.
- **fix(generate): don't lose the ruleset id on a fast success.** The poll loop occasionally saw the job flip to `success` a beat before `latest_ruleset_id` was populated, writing a null id to state and leaving `fields pull` / the field diff with nothing to work from. It now re-polls briefly for the id and only records a real one — never clobbering a prior good id with null.
- **example + e2e:** `examples/community-grants-rulebook/` is a generic rulebook (one shared field) with two member rulesets, and `tests/e2e/test_rulebook_hierarchy_e2e.py` (gated by the `manual` marker) drives discover/validate/generate/pull against a live API and asserts the shared rulebook field propagates into both members.
- No engine change required — all endpoints (`/fields/discover`, `/rulesets/{id}/schema`, `/fields/spec`) are already served by aethis-core and used by the MCP server.

## 0.21.0 (2026-06-16)

- **feat(init): field definitions get a real home (`fields/fields.yaml`).** `aethis init` now scaffolds a `fields/` directory with a `fields.yaml` for declaring the field vocabulary (key + `type` + optional `label`/`question`/`hints`). Previously fields had no dedicated home and only surfaced implicitly as the `inputs:` keys inside `tests/scenarios.yaml`. `aethis generate` reads `fields/fields.yaml`, pins the field keys/types via the project field-spec endpoint, and routes each field's label/question/hints through guidance so a field is defined once.
- **feat(init): `--kind rulebook` scaffolds a rulebook.** `aethis init <name> --kind rulebook` lays down a rulebook directory with shared `guidance/` and `fields/` plus a `rulesets/` directory for member rulesets. When a ruleset lives under a rulebook (`<rulebook>/rulesets/<ruleset>/`), `aethis generate` propagates the rulebook's guidance hints and field vocabulary into the ruleset — **the rulebook definition wins on shared field keys** — so a common field (e.g. date of birth) is defined once at the rulebook level and the end user is asked for it only once. `--kind` defaults to `ruleset`, so existing behaviour is unchanged.
  - New `AethisClient.set_field_spec()` (project field-spec endpoint, already served by aethis-core / used by the MCP server). No engine change required.

## 0.20.0 (2026-06-03)

- **feat(rulebooks list): anonymous fallthrough to the public rulebook catalogue.** With no cached API key, `aethis rulebooks list` now lists the cross-tenant public catalogue (rulebooks with public visibility, active status) instead of printing the v0.19.1 pointer message — completing the parity with `aethis rulesets list`. A dim one-liner ("No API key — showing public rulebooks…") distinguishes the anonymous view; with a key, the tenant listing is unchanged.
  - New `AethisClient.list_public_rulebooks()`; use with `make_anonymous_client` so a cached key doesn't promote the call to an authenticated tenant listing.
  - Requires aethis-core v0.29.0+ on the target API (live on api.aethis.ai). Against an older engine the anonymous path surfaces the server's 401 cleanly.

## 0.19.1 (2026-06-03)

- **fix(rulebooks list): stop prompting browser sign-in for anonymous users.** `aethis rulebooks list` with no cached API key used to trigger the lazy-auth browser login — bad first-contact DX for a read-only browse command. Rulebooks are tenant-scoped, so an anonymous caller has nothing to list; the command now prints a pointer to the anonymous public catalogue (`aethis rulesets list`) and to `aethis login`, and exits 1 without ever opening a browser.
  - True anonymous fallthrough (listing public rulebooks without an account, mirroring `aethis rulesets list`) needs engine support for a public rulebook catalogue and is tracked separately; this release removes the login prompt in the meantime.

## 0.19.0 (2026-06-03)

- **feat(update): `aethis update` — self-update the CLI to the latest release.** Detects how the CLI was installed (uv tool, pipx, or pip) and runs the matching upgrade command. `aethis update --check` reports whether a newer release exists without installing anything.
  - Editable (development) installs are refused with a pointer to `git pull && uv sync` instead of clobbering the checkout.
  - The exit-time "new release available" banner now points at `aethis update` rather than a method-specific command.
  - **fix:** the banner's uv upgrade hint was `uv tool install --upgrade aethis-cli`, which re-resolves from scratch and silently drops any extra `--with` requirements (e.g. plugin packages installed alongside the CLI). Both the banner's install-method detection and `aethis update` now use `uv tool upgrade aethis-cli`, which honours the original install receipt.
  - A successful (or no-op) `aethis update` refreshes the banner's 24h cache, so the notice goes quiet immediately after updating.

## 0.18.0 (2026-05-29)

- **feat(refine): `aethis refine` + `aethis generate --mode refine` for incremental, seed-from-existing re-authoring.** Instead of re-authoring a whole section from scratch, refine seeds generation from the section's active ruleset and makes the **minimal edit** to fix failing tests while keeping passing tests green.
  - `aethis refine [--hint "..."] [--seed-ruleset-id <id>]` — the phase-3 TDD-loop command: optionally add a guidance hint, then refine. Defaults to seeding from the section's active ruleset.
  - `aethis generate --mode refine [--seed-ruleset-id <id>]` — the same capability via a flag on `generate`; `--mode fresh` (default) is unchanged from-scratch authoring.
  - `AethisClient.generate()` gains optional `mode` / `seed_ruleset_id`; a no-arg call still sends no body, so it stays backwards-compatible against engines without the parameter.
  - Requires aethis-core with the `mode` parameter on `/generate` (live on `api.aethis.ai`). Against an older engine the flags no-op (empty body = fresh).

## 0.17.0 (2026-05-27)

- **feat(output): gh-style machine-readable output mode (`--output json`, `--json fields`, `--jq`).** Every list/show command (and the decision commands) now emit structured JSON on demand, so `aethis rulesets list --output json | jq '.[0].slug'` just works instead of trying to scrape ANSI-coloured Rich tables.
  - `--output table|json` — pick the format. Default: `table` on a TTY, `json` when piped (matches gh's pipe-friendly autodetect).
  - `--json FIELDS` — implies `--output json`; takes a required comma-separated value (`--json id,name`) that limits the payload to those fields. (gh's bare-`--json` introspection trick is not yet exposed — Click/Typer's option parser can't cleanly distinguish "flag with no value" from "flag followed by positional", so it's deferred to a future `--list-fields` flag.)
  - `--jq EXPR` — pipe JSON output through `jq` before printing. Requires the `jq` binary on PATH; clear error with install hint if missing.
  - Commands migrated: `rulesets list/show`, `rulebooks list/show/get-fields/tests list/schema/explain/decide`, `projects list/show`, `account keys`, `profile list`, `guidance list`, `fields`, `explain`, `decide`, `status`. Each command has a sensible JSON shape — `status --output json | jq .identity.key_id` returns the live key id without rooting through any prose.
  - Footer hints (`Try: aethis ...`) are suppressed in JSON mode so pipes get clean output.
  - New module `aethis_cli/render.py` is the single emit point; new test file `tests/test_render.py` covers the matrix.
- **breaking(guidance export): `--output` renamed to `--output-file`** to avoid clashing with the new global `--output` flag. Short form `-o` unchanged. Affects scripts that pipe to a named file: `aethis guidance export --output foo.yaml` → `aethis guidance export --output-file foo.yaml` (or `-o foo.yaml`).

## 0.16.3 (2026-05-27)

- **fix(status, whoami): read the multi-profile credentials file the same way every other command does.** `aethis login --api-key ...` writes `profiles.<name>.api_key` to `~/.config/aethis/credentials` (the multi-profile schema introduced in v0.10), but `aethis status` and `aethis whoami` had stale local resolvers that only looked for a flat top-level `api_key` (and `whoami` was looking at the wrong filename, `credentials.yaml`). Result: after a fresh `aethis login`, `aethis status` reported `no API key` and `aethis whoami` reported `No Aethis API key configured`, even though the same key worked for `aethis projects list`, `aethis generate`, and every other authoring command.
  - Both commands now route through the canonical `resolve_cached_key()` helper in `auth_helpers.py`, which honours `AETHIS_API_KEY` env → active profile → keychain → legacy `.yaml` file.
  - The `_resolve_cached_key` symbol is renamed to `resolve_cached_key` (public). The legacy `_resolve_key_silent` (status_cmd) and `_resolve_api_key_lax` (whoami_cmd) are removed.
  - Regression test in `tests/test_status_cmd.py` writes a real multi-profile credentials YAML to a temp `XDG_CONFIG_HOME` and asserts both commands surface the key.

## 0.16.2 (2026-05-22)

- **docs(readme): v0.27.0 accuracy pass.** Three fixes for fresh-developer accuracy:
  - Install block: removed the `pip install` fallback (uv and pipx are the recommended forms per workspace policy). Development section: `pip install -e ".[dev]"` → `uv pip install -e ".[dev]"`.
  - Added **Rulebooks** command-group section documenting the converged 2-term model surface shipped in v0.14.0–v0.16.1 (`aethis rulebooks` + `aethis rulesets` promote-to-live).
  - Updated engine_version example to `aethis-core@0.27.0` (was absent; clarified to current production version).

## 0.16.1 (2026-05-22)

- **docs(rulebooks set-logic):** the docstring example for `field_ref.key` now matches engine behaviour. Phase A.16 (aethis-core v0.26.0+) added per-section aggregate group synthesis, so `field_ref.key = <ruleset_name>` resolves to the AND of that ruleset's groups. The unscoped group-name and scoped `<ruleset_name>.<group>` forms remain available for advanced compositions. Requires aethis-core v0.26.0+ live on the target API.

## 0.16.0 (2026-05-21)

- **feat(rulebooks): `aethis rulebooks set-logic` — set the composition expression on a rulebook.** The composition expression (server field `outcome_logic`) is an Expr AST that combines per-ruleset outcomes into the rulebook's final decision. Previously settable only via raw PATCH; now exposed via the CLI for multi-ruleset rulebooks (e.g. UK FSM's `child_eligibility AND (household_criteria OR universal_infant)`).
  - `aethis rulebooks set-logic <id> -f logic.yaml` — load from YAML/JSON file
  - `aethis rulebooks set-logic <id> --logic '<json>'` — inline JSON
  - Exactly one of `--file` / `--logic` is required; both forms reject non-object payloads at the client side so server validation isn't the first line of defence.

## 0.15.0 (2026-05-21)

- **feat(rulesets): ruleset lifecycle commands scoped to a rulebook.** Phase B.1b of the converged 2-term model. Adds four new sub-commands under `aethis rulesets`:
  - `aethis rulesets list <rulebook>` — list rulesets in a rulebook (grouped by `ruleset_name` with version counts, live version, and observed states). The legacy `-p <project_id>` and `--public` modes are preserved while the project-scoped authoring pipeline retires in a future phase.
  - `aethis rulesets create <rulebook> <ruleset_name> [-n "Display name"]` — create a new draft Ruleset inside the rulebook. The display name auto-derives from `ruleset_name` if not provided (`child_eligibility` → `Child Eligibility`).
  - `aethis rulesets show <rulebook> <ruleset_name>` — full version history for one ruleset name (bundle_id, version, state, created), with live version highlighted.
  - `aethis rulesets promote-to-live <rulebook> <ruleset_name> <ruleset_id> [--note "..."]` — atomically promote a `testing`-state ruleset version to `live` via the Phase A.4 service. Auto-cuts a new rulebook version; previous live ruleset is archived.
- feat(client): four new `AethisClient` methods — `create_ruleset_in_rulebook`, `list_rulesets_in_rulebook`, `show_ruleset_in_rulebook`, `promote_ruleset_to_live`.
- Requires aethis-core v0.20.0+ live on the target API (Phase A.8 endpoints).

## 0.14.0 (2026-05-21)

- **feat(rulebooks): new `aethis rulebooks` command group.** First user-facing surface for the converged 2-term authoring model (workspace PR #64, aethis-core PRs #133-139). A Rulebook is the whole form — the execution unit — that owns a locked field vocabulary, composition logic, rulebook-level test cases, and an integer version history.
  - `aethis rulebooks list` — list tenant rulebooks
  - `aethis rulebooks show <id-or-slug>` — full configuration
  - `aethis rulebooks create <name> --domain <d> [--slug ...]` — create draft
  - `aethis rulebooks set-fields <id> -f fields.yaml` — replace locked vocabulary
  - `aethis rulebooks lock-fields <id>` / `unlock-fields <id>` / `get-fields <id>`
  - `aethis rulebooks tests add <id> -f scenario.yaml` — embed full-form test case
  - `aethis rulebooks tests list <id>` / `delete <id> <tc_id>`
  - `aethis rulebooks activate <id>` / `archive <id>` — lifecycle
  - `aethis rulebooks decide <id> -i '{...}' [--explain]` — evaluate composed rulebook
  - `aethis rulebooks schema <id>` / `explain <id>` — combined schema + explanations
- feat(client): new `AethisClient` methods for every rulebook REST endpoint (create / list / show / update / activate / archive / set-fields / lock-fields / unlock-fields / get-fields / add-test / list-tests / delete-test / decide-rulebook / get-rulebook-schema / explain-rulebook).
- Requires aethis-core v0.19.0+ live on the target API (the Phase A.6 endpoints).
- The legacy `aethis projects` / `aethis generate` / `aethis test` / `aethis publish` command tree is unchanged in this release — replacement lands in the next minor (Phase B.1b: ruleset lifecycle + project retirement). No backward-compat shims are planned past public release.

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
- fix: update `tests/e2e/test_spacecraft_e2e.py` to resolve the spacecraft fixture from `examples/spacecraft-crew-rules/` instead of an internal path; drop internal service name from comment
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
