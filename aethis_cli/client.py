"""Thin HTTP client for the Aethis developer API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from aethis_cli.errors import AethisAPIError

# Callback signature for the lazy-auth refresh hook. Receives ``force_browser``
# (always True at the call site — we know the cached key just failed) and
# returns a fresh API key, or raises :class:`AuthRequired` to abort cleanly.
KeyRefreshCallback = Callable[..., str]


class AethisClient:
    """Synchronous client wrapping all Aethis API endpoints."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.aethis.ai",
        anthropic_key: Optional[str] = None,
        on_auth_required: Optional[KeyRefreshCallback] = None,
    ) -> None:
        headers: dict[str, str] = {"X-API-Key": api_key}
        if anthropic_key:
            headers["X-Anthropic-Key"] = anthropic_key
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=60.0,
            verify=True,
        )
        # Hook called once when the server returns 401. If it returns a new
        # key the request is retried exactly once with the refreshed header;
        # a second 401 surfaces the original error so we never loop.
        self._on_auth_required = on_auth_required

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

    def decide(self, bundle_id: str, field_values: dict, **opts: Any) -> dict:
        return self._request(
            "POST",
            "/api/v1/public/decide",
            json={
                "bundle_id": bundle_id,
                "field_values": field_values,
                **opts,
            },
        )

    def whoami(self) -> dict:
        """Return metadata for the current API key."""
        return self._request("GET", "/api/v1/public/me")

    def get_schema(self, bundle_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/bundles/{bundle_id}/schema")

    def explain(self, bundle_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/bundles/{bundle_id}/explain")

    def get_source(self, bundle_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/bundles/{bundle_id}/source")

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

    def publish(self, project_id: str, *, slug: str | None = None) -> dict:
        body: dict = {}
        if slug is not None:
            body["slug"] = slug
        kwargs: dict = {}
        if body:
            kwargs["json"] = body
        return self._request(
            "POST",
            f"/api/v1/public/projects/{project_id}/publish",
            **kwargs,
        )

    def list_bundles(self, project_id: str, status: str | None = None) -> list[dict]:
        params: dict[str, str] = {}
        if status:
            params["status"] = status
        return self._request("GET", f"/api/v1/public/projects/{project_id}/bundles", params=params)

    def archive_project(self, project_id: str) -> dict:
        return self._request("POST", f"/api/v1/public/projects/{project_id}/archive")

    def archive_bundle(self, bundle_id: str) -> dict:
        return self._request("POST", f"/api/v1/public/bundles/{bundle_id}/archive")

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
