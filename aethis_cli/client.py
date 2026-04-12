"""Thin HTTP client for the Aethis developer API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from aethis_cli.errors import AethisAPIError


class AethisClient:
    """Synchronous client wrapping all Aethis API endpoints."""

    def __init__(self, api_key: str, base_url: str = "https://api.aethis.ai") -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={"X-API-Key": api_key},
            timeout=60.0,
            verify=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AethisClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except (ValueError, KeyError):
                detail = resp.text or f"HTTP {resp.status_code}"
            raise AethisAPIError(resp.status_code, detail)
        if resp.status_code == 204:
            return {}
        return resp.json()

    # -- Decision API --

    def decide(self, bundle_id: str, field_values: dict, **opts: Any) -> dict:
        return self._request("POST", "/api/v1/public/decide", json={
            "bundle_id": bundle_id,
            "field_values": field_values,
            **opts,
        })

    def get_schema(self, bundle_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/bundles/{bundle_id}/schema")

    def explain(self, bundle_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/bundles/{bundle_id}/explain")

    def get_source(self, bundle_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/bundles/{bundle_id}/source")

    # -- Projects API --

    def create_project(self, name: str, section_id: str, domain: str = "") -> dict:
        return self._request("POST", "/api/v1/public/projects/", json={
            "name": name,
            "section_id": section_id,
            "domain": domain,
        })

    def list_projects(self, include_archived: bool = False) -> list[dict]:
        params: dict[str, str] = {}
        if include_archived:
            params["include_archived"] = "true"
        return self._request("GET", "/api/v1/public/projects/", params=params)

    def get_project(self, project_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/projects/{project_id}")

    def add_guidance(self, project_id: str, guidance_text: str, source: str = "human") -> dict:
        return self._request("POST", f"/api/v1/public/projects/{project_id}/guidance", json={
            "guidance_text": guidance_text,
            "source": source,
        })

    def list_guidance(self, project_id: str) -> list:
        return self._request("GET", f"/api/v1/public/projects/{project_id}/guidance")

    def export_guidance(self, project_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/projects/{project_id}/guidance/export")

    def deactivate_guidance(self, project_id: str, hint_id: str) -> dict:
        return self._request("DELETE", f"/api/v1/public/projects/{project_id}/guidance/{hint_id}")

    def update_guidance(self, project_id: str, hint_id: str, guidance_text: str) -> dict:
        return self._request("PATCH", f"/api/v1/public/projects/{project_id}/guidance/{hint_id}", json={
            "guidance_text": guidance_text,
        })

    def upload_sources(self, project_id: str, files: list[Path]) -> dict:
        file_tuples = [
            ("files", (f.name, f.read_bytes(), "application/octet-stream"))
            for f in files
        ]
        return self._request("POST", f"/api/v1/public/projects/{project_id}/sources", files=file_tuples)

    def add_tests(self, project_id: str, test_cases: list[dict]) -> dict:
        return self._request("POST", f"/api/v1/public/projects/{project_id}/tests", json={
            "test_cases": test_cases,
        })

    def generate(self, project_id: str) -> dict:
        return self._request("POST", f"/api/v1/public/projects/{project_id}/generate")

    def get_status(self, project_id: str) -> dict:
        return self._request("GET", f"/api/v1/public/projects/{project_id}/status")

    def run_tests(self, project_id: str) -> dict:
        return self._request("POST", f"/api/v1/public/projects/{project_id}/test-run")

    def publish(self, project_id: str) -> dict:
        return self._request("POST", f"/api/v1/public/projects/{project_id}/publish")

    def list_bundles(self, project_id: str, status: str | None = None) -> list[dict]:
        params: dict[str, str] = {}
        if status:
            params["status"] = status
        return self._request("GET", f"/api/v1/public/projects/{project_id}/bundles", params=params)

    def archive_project(self, project_id: str) -> dict:
        return self._request("POST", f"/api/v1/public/projects/{project_id}/archive")

    def archive_bundle(self, bundle_id: str) -> dict:
        return self._request("POST", f"/api/v1/public/bundles/{bundle_id}/archive")
