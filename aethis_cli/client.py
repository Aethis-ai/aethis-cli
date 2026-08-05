"""Thin HTTP client for the Aethis developer API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from aethis_cli._version import __version__
from aethis_cli.auth_providers import AuthProvider, ProviderContext, get_provider
from aethis_cli.contract import check_decide_options
from aethis_cli.errors import AethisAPIError

# Callback signature for the lazy-auth refresh hook. Receives ``force_browser``
# (always True at the call site — we know the cached key just failed) and
# returns a fresh API key, or raises :class:`AuthRequired` to abort cleanly.
KeyRefreshCallback = Callable[..., str]

# Marker for "this client has not asked the engine yet". Distinct from both
# True/False (asked, got an answer) and None (asked, could not tell) — the
# three states a capability probe genuinely has.
_UNPROBED = object()


class AethisClient:
    """Synchronous client wrapping all Aethis API endpoints."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.aethis.ai",
        anthropic_key: Optional[str] = None,
        on_auth_required: Optional[KeyRefreshCallback] = None,
        *,
        unsigned: bool = False,
        auth_provider: Optional[AuthProvider] = None,
        profile: Optional[dict] = None,
    ) -> None:
        # Auth resolution: an explicit ``auth_provider`` wins (the staff plugin
        # passes its gcloud-ID-token provider this way). Otherwise we keep
        # legacy behaviour: ``unsigned=True`` => no auth header; api_key
        # present => ``X-API-Key`` header. Every existing call site that just
        # passes ``(api_key, base_url)`` keeps working unchanged.
        if auth_provider is None:
            auth_provider = get_provider("none" if unsigned else "api_key")
        ctx = ProviderContext(
            api_key=None if unsigned else api_key,
            base_url=base_url,
            profile=profile or {},
        )
        headers: dict[str, str] = dict(auth_provider(ctx))
        # Per-surface telemetry: identify the calling client + its version so the
        # server can attribute `/review` (and future) records to `cli` vs `mcp`.
        # Always sent — the header carries no credentials and no PII.
        headers["X-Aethis-Client"] = f"cli/{__version__}"
        if anthropic_key:
            headers["X-Anthropic-Key"] = anthropic_key
        self._auth_provider = auth_provider
        self._auth_ctx = ctx
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=60.0,
            verify=True,
        )
        # Hook called once when the server returns 401. If it returns a new
        # key the request is retried exactly once with the refreshed header;
        # a second 401 surfaces the original error so we never loop. Disabled
        # for unsigned clients — there's no key to refresh.
        self._on_auth_required = None if unsigned else on_auth_required
        # Latest X-RateLimit-* budget seen on a response (epic #552 P2). None
        # until the first metered, authenticated call.
        self.last_rate_limit: Optional[dict] = None
        # The engine this client talks to. Artefact-backed source references
        # carry a RELATIVE authenticated download path, which is only
        # meaningful joined to the host it was published on — so renderers
        # need to be able to ask.
        self.base_url = base_url
        # Cached answer to "does this engine accept `replace` on a test-case
        # upload?" — one probe per client, not one per call.
        self._test_replace_support: Any = _UNPROBED

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AethisClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _capture_rate_limit(self, resp: httpx.Response) -> None:
        """Stash the X-RateLimit-* headers (epic #552 P2) from the latest
        response so command code can surface remaining budget. None when the
        server didn't send them (unauthenticated / non-metered response)."""
        h = resp.headers
        cls = h.get("X-RateLimit-Class")
        if cls is None:
            return
        try:
            self.last_rate_limit = {
                "class": cls,
                "limit": int(h["X-RateLimit-Limit"]),
                "remaining": int(h["X-RateLimit-Remaining"]),
                "reset": int(h["X-RateLimit-Reset"]),
            }
        except (KeyError, ValueError):
            self.last_rate_limit = None

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code == 401 and self._on_auth_required is not None:
            # Single retry. Disable the hook for the retry to bound recursion
            # at one extra round-trip even if the hook's caller plumbs the
            # wrong key back in. ``AuthRequired`` (no TTY, ``--no-prompt``,
            # user declined) and other refresh-flow exceptions propagate so
            # the CLI wrapper can render a clean one-liner instead of being
            # masked by the original 401.
            refresh = self._on_auth_required
            self._on_auth_required = None
            new_key = refresh(force_browser=True)
            self._client.headers["X-API-Key"] = new_key
            resp = self._client.request(method, path, **kwargs)
        self._capture_rate_limit(resp)
        if resp.status_code >= 400:
            self._raise_for_status(resp)
        if resp.status_code == 204:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            # A 2xx whose body is not JSON is a broken server, an HTML error
            # page from an intermediary, or a truncated response. Any of those
            # is an API error the CLI reports in one line -- never a raw
            # JSONDecodeError traceback out of the middle of a command.
            preview = (resp.text or "").strip()[:200]
            raise AethisAPIError(
                resp.status_code,
                "The server returned a non-JSON response"
                + (f": {preview}" if preview else " with an empty body")
                + ". If this persists, the API or an intermediary between you "
                "and it is misbehaving.",
            ) from exc

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        try:
            detail = resp.json().get("detail", resp.text)
        except (ValueError, KeyError):
            detail = resp.text or f"HTTP {resp.status_code}"
        raise AethisAPIError(resp.status_code, detail)

    # -- Decision API --

    def decide(self, ruleset_id: str, field_values: dict, **opts: Any) -> dict:
        """Evaluate ``field_values`` against a published ruleset.

        The API rejects an unknown top-level body member with a 422 rather
        than ignoring it, so an undeclared option is caught here and named
        locally instead of costing a round-trip.
        """
        check_decide_options(opts)
        return self._request(
            "POST",
            "/api/v1/public/decide",
            json={
                "ruleset_id": ruleset_id,
                "field_values": field_values,
                **opts,
            },
        )

    def usage(self) -> dict:
        """Per-operation-class rate-limit budget for the calling key (epic #552)."""
        return self._request("GET", "/api/v1/public/usage")

    def whoami(self) -> dict:
        """Return metadata for the current API key."""
        return self._request("GET", "/api/v1/public/me")

    def get_schema(self, ruleset_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/rulesets/{ruleset_id}/schema")

    def explain(self, ruleset_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/rulesets/{ruleset_id}/explain")

    def get_source(self, ruleset_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/rulesets/{ruleset_id}/source")

    # -- Projects API --

    def create_project(self, name: str, section_id: str, domain: str = "") -> dict:
        return self._request(
            "POST",
            "/api/v1/public/projects/",
            json={
                "name": name,
                "section_id": section_id,
                "domain": domain,
            },
        )

    def list_projects(self, include_archived: bool = False) -> list[dict]:
        params: dict[str, str] = {}
        if include_archived:
            params["include_archived"] = "true"
        return self._request("GET", "/api/v1/public/projects/", params=params)

    def get_project(self, project_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/projects/{project_id}")

    def add_guidance(
        self,
        project_id: str,
        guidance_text: str,
        source: str = "human",
        process_type: str = "rule_generation",
    ) -> dict:
        return self._request(
            "POST",
            f"/api/v1/public/projects/{project_id}/guidance",
            json={
                "guidance_text": guidance_text,
                "source": source,
                "process_type": process_type,
            },
        )

    def list_guidance(self, project_id: str) -> list:
        return self._request("GET", f"/api/v1/public/projects/{project_id}/guidance")

    def export_guidance(self, project_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/projects/{project_id}/guidance/export")

    def deactivate_guidance(self, project_id: str, hint_id: str) -> dict:
        return self._request("DELETE", f"/api/v1/public/projects/{project_id}/guidance/{hint_id}")

    def update_guidance(self, project_id: str, hint_id: str, guidance_text: str) -> dict:
        return self._request(
            "PATCH",
            f"/api/v1/public/projects/{project_id}/guidance/{hint_id}",
            json={
                "guidance_text": guidance_text,
            },
        )

    def upload_sources(self, project_id: str, files: list[Path]) -> dict:
        file_tuples = [("files", (f.name, f.read_bytes(), "application/octet-stream")) for f in files]
        return self._request("POST", f"/api/v1/public/projects/{project_id}/sources", files=file_tuples)

    def list_sources(self, project_id: str) -> dict:
        """List the project's uploaded sources (metadata only, no bodies).

        Each entry carries ``raw_sha256`` — the digest of the exact bytes the
        engine retained — which is what lets a caller cite an already-uploaded
        file instead of uploading a second identical copy. The engine never
        dedupes on upload, so this is the only guard against duplicates.
        """
        return self._request("GET", f"/api/v1/public/projects/{project_id}/sources")

    def add_tests(self, project_id: str, test_cases: list[dict], replace: bool = False) -> dict:
        """Upload golden test cases to a project.

        ``replace=True`` makes the upload idempotent: the supplied list becomes
        the project's test cases instead of being added to whatever is already
        there, and the response reports ``replaced`` — how many existing cases
        were overwritten — alongside ``added``.

        Only send it to an engine that advertises it (see
        :meth:`supports_test_replace`). An engine without the member does not
        reject the request; it ignores it and appends, which is the silent
        duplication this flag exists to stop. The member is therefore omitted
        entirely rather than sent as ``false``, so an engine's own default
        decides — and so nothing is ever sent that could be quietly dropped.
        """
        body: dict = {"test_cases": test_cases}
        if replace:
            body["replace"] = True
        return self._request(
            "POST",
            f"/api/v1/public/projects/{project_id}/tests",
            json=body,
        )

    def supports_test_replace(self) -> Optional[bool]:
        """Does the engine this client talks to accept ``replace`` on a test upload?

        Answered from the engine's own published schema — ``replace`` under
        ``components.schemas.AddTestCaseRequest.properties`` — rather than from
        the CLI's version, because the two are deployed independently and the
        same CLI is pointed at engines of different vintages.

        Returns ``True``/``False`` when the schema answered, and ``None`` when
        it could not be read at all. **Unknown is not unsupported**: the caller
        must say which of the two it got, because both mean the upload appends
        and only one of them means the engine is old.
        """
        if self._test_replace_support is not _UNPROBED:
            return self._test_replace_support  # type: ignore[return-value]
        answer: Optional[bool]
        try:
            resp = self._client.get("/openapi.json", timeout=15.0)
            if resp.status_code >= 400:
                answer = None
            else:
                schemas = resp.json()["components"]["schemas"]
                properties = schemas["AddTestCaseRequest"]["properties"]
                answer = "replace" in properties
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # Unreachable, non-JSON (an intermediary's error page), or a schema
            # shaped differently than expected. None of those is evidence about
            # the engine's behaviour, so none of them may read as an answer.
            answer = None
        self._test_replace_support = answer
        return answer

    def set_field_spec(self, project_id: str, expected_fields: list[dict]) -> dict:
        """Pin the project's expected field vocabulary (key + type + enum values).

        Each entry is ``{key, sort, enum_values?}``. This constrains generation
        to the declared field keys so the same field (e.g. date of birth) is not
        re-invented under a different key. Field hints / questions are carried as
        guidance, not here — this endpoint only fixes the vocabulary.
        """
        return self._request(
            "POST",
            f"/api/v1/public/projects/{project_id}/fields/spec",
            json={"expected_fields": expected_fields},
        )

    def discover_fields(self, project_id: str) -> dict:
        """Run server-side field discovery over the project's uploaded sources.

        LLM-powered: the Anthropic key rides on the ``X-Anthropic-Key`` header
        set at client construction (same path as ``generate``). Returns the
        discovered field candidates plus a completeness score and gaps.
        """
        return self._request("POST", f"/api/v1/public/projects/{project_id}/fields/discover", json={})

    def generate(
        self,
        project_id: str,
        mode: Optional[str] = None,
        seed_ruleset_id: Optional[str] = None,
    ) -> dict:
        """Trigger generation. ``mode="refine"`` seeds from the section's active
        ruleset and makes the minimal edit to fix failing tests; omitting ``mode``
        (or ``mode="fresh"``) authors from scratch. A no-arg call sends no body, so
        it stays backwards-compatible against engines without the ``mode`` parameter.
        """
        body: dict = {}
        if mode is not None:
            body["mode"] = mode
        if seed_ruleset_id is not None:
            body["seed_ruleset_id"] = seed_ruleset_id
        kwargs = {"json": body} if body else {}
        return self._request("POST", f"/api/v1/public/projects/{project_id}/generate", **kwargs)

    def get_status(self, project_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/projects/{project_id}/status")

    def run_tests(self, project_id: str) -> dict:
        return self._request("POST", f"/api/v1/public/projects/{project_id}/test-run")

    def review(self, project_id: str, *, coach: bool = False) -> dict:
        """Run the Authoring Coach rubric over a project.

        Returns the deterministic ``ReviewReport`` (objective checks, a
        reproducible score, and the single author-actionable ``next_skill``).
        ``coach=True`` opts into LLM mentoring prose — the server requires the
        author's own ``X-Anthropic-Key`` (set at client construction) for
        external keys; the deterministic report needs no key.
        """
        return self._request(
            "POST",
            f"/api/v1/public/projects/{project_id}/review",
            json={"coach": coach},
        )

    def publish(
        self,
        project_id: str,
        *,
        slug: str | None = None,
        force_unsafe: bool = False,
        rulebook_id: str | None = None,
        ruleset_name: str | None = None,
        source_targets: dict[str, dict] | None = None,
    ) -> dict:
        body: dict = {}
        if slug is not None:
            body["slug"] = slug
        if force_unsafe:
            # Tell the server-side TDD gate (aethis-core 0.11+) to refuse a
            # publish when stored test cases fail; force_unsafe=True records
            # an audit event and proceeds. Older engines ignore the field.
            body["force_unsafe"] = True
        # Phase A.9 — publish-into-rulebook bridge. When both set, the
        # produced ruleset is stamped with rulebook_id + ruleset_name and
        # lands in state="testing" (instead of status="active"). Promotion
        # to live then flows via /rulebooks/{id}/rulesets/{name}/promote-
        # to-live. Requires aethis-core v0.21.0+; older engines either
        # ignore the fields silently or 422 on the model_validator.
        if rulebook_id is not None:
            body["rulebook_id"] = rulebook_id
        if ruleset_name is not None:
            body["ruleset_name"] = ruleset_name
        # Citation resolution targets, keyed by the opaque citation key the
        # ruleset's criteria declare in `source_refs`. Each value names
        # exactly one target: `url` (HTTPS, fetched + snapshotted at publish,
        # emitted as a schema-v1 reference) or `artefact_source_id` (an
        # uploaded project source, resolved from retained bytes with no
        # network call, emitted as a schema-v2 reference). The engine fails
        # the publish closed on any unresolved/unlicensed/quote-mismatched
        # key. Requires an engine carrying artefact targets for the
        # `artefact_source_id` form; older engines accept the `url` form.
        if source_targets:
            body["source_targets"] = source_targets
        kwargs: dict = {}
        if body:
            kwargs["json"] = body
        return self._request(
            "POST",
            f"/api/v1/public/projects/{project_id}/publish",
            **kwargs,
        )

    def list_rulesets(self, project_id: str, status: str | None = None) -> list[dict]:
        params: dict[str, str] = {}
        if status:
            params["status"] = status
        return self._request("GET", f"/api/v1/public/projects/{project_id}/rulesets", params=params)

    def list_public_rulesets(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """List published rulesets visible to anonymous callers.

        Hits the cross-tenant catalogue (``visibility="public"`` only). Works
        whether the client was constructed with ``unsigned=True`` or with a
        valid key — the server filters on visibility either way.
        """
        return self._request(
            "GET",
            "/api/v1/public/rulesets",
            params={"limit": str(limit), "offset": str(offset)},
        )

    def archive_project(self, project_id: str) -> dict:
        return self._request("POST", f"/api/v1/public/projects/{project_id}/archive")

    def archive_ruleset(self, ruleset_id: str) -> dict:
        return self._request("POST", f"/api/v1/public/rulesets/{ruleset_id}/archive")

    # -- Rulebooks API (Phase B.1 — converged 2-term model) --

    def create_rulebook(
        self,
        name: str,
        *,
        domain: str = "",
        description: Optional[str] = None,
        slug: Optional[str] = None,
        ruleset_refs: Optional[list[dict]] = None,
        outcome_logic: Optional[dict] = None,
        robot_hints: Optional[dict] = None,
    ) -> dict:
        body: dict[str, Any] = {
            "name": name,
            "domain": domain,
            "ruleset_refs": ruleset_refs or [],
        }
        if description is not None:
            body["description"] = description
        if slug is not None:
            body["slug"] = slug
        if outcome_logic is not None:
            body["outcome_logic"] = outcome_logic
        if robot_hints is not None:
            body["robot_hints"] = robot_hints
        return self._request("POST", "/api/v1/public/rulebooks/", json=body)

    def list_rulebooks(self) -> list[dict]:
        return self._request("GET", "/api/v1/public/rulebooks/")

    def list_public_rulebooks(self) -> list[dict]:
        """List rulebooks visible to anonymous callers.

        Hits the cross-tenant catalogue (``visibility="public"`` AND
        ``status="active"``) on the same route as :meth:`list_rulebooks` —
        the server keys the behaviour on the absence of an API key. Use
        with :func:`make_anonymous_client` so a cached key doesn't promote
        the call to an authenticated tenant listing. Requires aethis-core
        v0.29.0+ on the target API.
        """
        return self._request("GET", "/api/v1/public/rulebooks/")

    def get_rulebook(self, rulebook_id_or_slug: str) -> dict:
        return self._request("GET", f"/api/v1/public/rulebooks/{rulebook_id_or_slug}")

    def update_rulebook(
        self,
        rulebook_id_or_slug: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        ruleset_refs: Optional[list[dict]] = None,
        outcome_logic: Optional[dict] = None,
        slug: Optional[str] = None,
        robot_hints: Optional[dict] = None,
    ) -> dict:
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if ruleset_refs is not None:
            body["ruleset_refs"] = ruleset_refs
        if outcome_logic is not None:
            body["outcome_logic"] = outcome_logic
        if slug is not None:
            body["slug"] = slug
        if robot_hints is not None:
            body["robot_hints"] = robot_hints
        return self._request(
            "PATCH",
            f"/api/v1/public/rulebooks/{rulebook_id_or_slug}",
            json=body,
        )

    def activate_rulebook(self, rulebook_id_or_slug: str) -> dict:
        return self._request("POST", f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/activate")

    def archive_rulebook(self, rulebook_id_or_slug: str) -> dict:
        return self._request("POST", f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/archive")

    def get_rulebook_schema(self, rulebook_id_or_slug: str) -> dict:
        return self._request("GET", f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/schema")

    def explain_rulebook(self, rulebook_id_or_slug: str) -> dict:
        return self._request("GET", f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/explain")

    def get_rulebook_graph(self, rulebook_id_or_slug: str, *, format: str = "all") -> dict:
        """Fetch the rulebook-level ruleset-map graph (field -> criterion -> group -> outcome).

        Returns ``{rulebook_id, graph: {nodes, edges, sections, stats}, mermaid}``.
        Unlike :meth:`get_ruleset_graph`, this endpoint requires a valid API
        key even for a public rulebook (confirmed against the live engine,
        2026-07-17) — there is no anonymous fallback here.
        """
        params = {} if format == "all" else {"format": format}
        return self._request(
            "GET",
            f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/graph",
            params=params,
        )

    def get_ruleset_graph(self, ruleset_id: str, *, format: str = "all") -> dict:
        """Fetch the single-ruleset dependency graph (field -> criterion -> group -> outcome).

        Returns ``{ruleset_id, slug, name, graph: {nodes}, mermaid}``. Public
        rulesets can be inspected without an API key — pair with
        :func:`make_anonymous_client`.
        """
        params = {} if format == "all" else {"format": format}
        return self._request(
            "GET",
            f"/api/v1/public/rulesets/{ruleset_id}/graph",
            params=params,
        )

    # -- Rulebook fields (Phase A.6) --

    def set_rulebook_fields(self, rulebook_id_or_slug: str, fields: list[dict]) -> dict:
        return self._request(
            "POST",
            f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/fields",
            json={"fields": fields},
        )

    def lock_rulebook_fields(self, rulebook_id_or_slug: str) -> dict:
        return self._request(
            "POST",
            f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/fields/lock",
        )

    def unlock_rulebook_fields(self, rulebook_id_or_slug: str) -> dict:
        return self._request(
            "POST",
            f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/fields/unlock",
        )

    def get_rulebook_fields(self, rulebook_id_or_slug: str) -> dict:
        return self._request("GET", f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/fields")

    # -- Rulebook-level test cases (Phase A.6) --

    def add_rulebook_test_case(
        self,
        rulebook_id_or_slug: str,
        *,
        name: str,
        field_values: dict,
        expected_outcome: str,
    ) -> dict:
        return self._request(
            "POST",
            f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/tests",
            json={
                "name": name,
                "field_values": field_values,
                "expected_outcome": expected_outcome,
            },
        )

    def list_rulebook_test_cases(self, rulebook_id_or_slug: str) -> dict:
        return self._request("GET", f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/tests")

    def delete_rulebook_test_case(self, rulebook_id_or_slug: str, tc_id: str) -> None:
        self._request(
            "DELETE",
            f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/tests/{tc_id}",
        )

    def decide_rulebook(self, rulebook_id_or_slug: str, field_values: dict, **opts: Any) -> dict:
        """Evaluate a rulebook against field_values.

        Same endpoint as :py:meth:`decide` but passes ``rulebook_id`` instead
        of ``ruleset_id``. The engine resolves the rulebook's live ruleset
        pins and runs the composed evaluation.
        """
        check_decide_options(opts)
        return self._request(
            "POST",
            "/api/v1/public/decide",
            json={
                "rulebook_id": rulebook_id_or_slug,
                "field_values": field_values,
                **opts,
            },
        )

    # -- Ruleset-within-rulebook lifecycle (Phase A.8) --

    def create_ruleset_in_rulebook(
        self,
        rulebook_id_or_slug: str,
        *,
        ruleset_name: str,
        name: str,
        python_source: Optional[str] = None,
    ) -> dict:
        body: dict[str, Any] = {
            "ruleset_name": ruleset_name,
            "name": name,
        }
        if python_source is not None:
            body["python_source"] = python_source
        return self._request(
            "POST",
            f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/rulesets",
            json=body,
        )

    def list_rulesets_in_rulebook(self, rulebook_id_or_slug: str) -> dict:
        return self._request(
            "GET",
            f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/rulesets",
        )

    def show_ruleset_in_rulebook(self, rulebook_id_or_slug: str, ruleset_name: str) -> dict:
        return self._request(
            "GET",
            f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/rulesets/{ruleset_name}",
        )

    def promote_ruleset_to_live(
        self,
        rulebook_id_or_slug: str,
        ruleset_name: str,
        *,
        ruleset_id: str,
        note: Optional[str] = None,
    ) -> dict:
        body: dict[str, Any] = {"ruleset_id": ruleset_id}
        if note is not None:
            body["note"] = note
        return self._request(
            "POST",
            f"/api/v1/public/rulebooks/{rulebook_id_or_slug}/rulesets/{ruleset_name}/promote-to-live",
            json=body,
        )

    # -- Domain guidance API --

    def add_domain_guidance(
        self,
        domain: str,
        guidance_text: str,
        process_type: str = "rule_generation",
        notes: Optional[str] = None,
    ) -> dict:
        body: dict[str, Any] = {"guidance_text": guidance_text, "process_type": process_type}
        if notes:
            body["notes"] = notes
        return self._request("POST", f"/api/v1/public/domains/{domain}/guidance", json=body)

    def list_domain_guidance(self, domain: str) -> list:
        return self._request("GET", f"/api/v1/public/domains/{domain}/guidance")


def make_anonymous_client(base_url: str = "https://api.aethis.ai") -> AethisClient:
    """Construct a key-less client for anonymous endpoints (public catalogue, decide).

    Skips the ``X-API-Key`` header and disables the 401-refresh hook. Use this
    in command paths that explicitly target the public surface so an admin's
    cached key doesn't accidentally promote anonymous calls to authenticated
    ones (which would leak their tenant's rulesets into the response).
    """
    return AethisClient(api_key=None, base_url=base_url, unsigned=True)
