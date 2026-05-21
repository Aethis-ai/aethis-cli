"""Thin HTTP client for the Aethis developer API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from aethis_cli.auth_providers import AuthProvider, ProviderContext, get_provider
from aethis_cli.errors import AethisAPIError

# Callback signature for the lazy-auth refresh hook. Receives ``force_browser``
# (always True at the call site — we know the cached key just failed) and
# returns a fresh API key, or raises :class:`AuthRequired` to abort cleanly.
KeyRefreshCallback = Callable[..., str]


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

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AethisClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

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
        if resp.status_code >= 400:
            self._raise_for_status(resp)
        if resp.status_code == 204:
            return {}
        return resp.json()

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        try:
            detail = resp.json().get("detail", resp.text)
        except (ValueError, KeyError):
            detail = resp.text or f"HTTP {resp.status_code}"
        raise AethisAPIError(resp.status_code, detail)

    # -- Decision API --

    def decide(self, ruleset_id: str, field_values: dict, **opts: Any) -> dict:
        return self._request(
            "POST",
            "/api/v1/public/decide",
            json={
                "ruleset_id": ruleset_id,
                "field_values": field_values,
                **opts,
            },
        )

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

    def add_tests(self, project_id: str, test_cases: list[dict]) -> dict:
        return self._request(
            "POST",
            f"/api/v1/public/projects/{project_id}/tests",
            json={
                "test_cases": test_cases,
            },
        )

    def generate(self, project_id: str) -> dict:
        return self._request("POST", f"/api/v1/public/projects/{project_id}/generate")

    def get_status(self, project_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/projects/{project_id}/status")

    def run_tests(self, project_id: str) -> dict:
        return self._request("POST", f"/api/v1/public/projects/{project_id}/test-run")

    def publish(
        self,
        project_id: str,
        *,
        slug: str | None = None,
        force_unsafe: bool = False,
    ) -> dict:
        body: dict = {}
        if slug is not None:
            body["slug"] = slug
        if force_unsafe:
            # Tell the server-side TDD gate (aethis-core 0.11+) to refuse a
            # publish when stored test cases fail; force_unsafe=True records
            # an audit event and proceeds. Older engines ignore the field.
            body["force_unsafe"] = True
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
        return self._request("POST", "/api/v1/public/rulebooks/", json=body)

    def list_rulebooks(self) -> list[dict]:
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
