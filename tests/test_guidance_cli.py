"""TDD tests for guidance CLI commands and client methods.

Tests written FIRST, then implement to make them pass.
"""

from __future__ import annotations

import httpx
import pytest
import respx
import yaml

from aethis_cli.client import AethisClient

BASE = "https://test.local"


# ============================================================================
# Client methods
# ============================================================================


@respx.mock(base_url=BASE)
def test_list_guidance(respx_mock):
    """Client should list guidance hints for a project."""
    respx_mock.get("/api/v1/public/projects/proj_1/guidance").mock(
        return_value=httpx.Response(200, json=[
            {"hint_id": "h1", "guidance_text": "Hint 1", "source": "human", "version": 1, "active": True, "weight": 0.5},
            {"hint_id": "h2", "guidance_text": "Hint 2", "source": "agent", "version": 1, "active": True, "weight": 0.3},
        ])
    )
    client = AethisClient("ak_live_test", BASE)
    result = client.list_guidance("proj_1")
    assert len(result) == 2
    assert result[0]["source"] == "human"
    assert result[1]["source"] == "agent"


@respx.mock(base_url=BASE)
def test_export_guidance(respx_mock):
    """Client should export guidance as a structured dict for YAML."""
    respx_mock.get("/api/v1/public/projects/proj_1/guidance/export").mock(
        return_value=httpx.Response(200, json={
            "hints": [
                {"text": "Human hint", "source": "human"},
                {"text": "Agent hint", "source": "agent"},
            ]
        })
    )
    client = AethisClient("ak_live_test", BASE)
    result = client.export_guidance("proj_1")
    assert len(result["hints"]) == 2

    # Should be valid YAML
    output = yaml.dump(result, default_flow_style=False)
    parsed = yaml.safe_load(output)
    assert parsed["hints"][0]["source"] == "human"


@respx.mock(base_url=BASE)
def test_deactivate_guidance(respx_mock):
    """Client should soft-deactivate a guidance hint."""
    respx_mock.delete("/api/v1/public/projects/proj_1/guidance/h1").mock(
        return_value=httpx.Response(200, json={"hint_id": "h1", "status": "deactivated"})
    )
    client = AethisClient("ak_live_test", BASE)
    result = client.deactivate_guidance("proj_1", "h1")
    assert result["status"] == "deactivated"


@respx.mock(base_url=BASE)
def test_update_guidance(respx_mock):
    """Client should update a hint (creates new version)."""
    respx_mock.patch("/api/v1/public/projects/proj_1/guidance/h1").mock(
        return_value=httpx.Response(200, json={
            "old_hint_id": "h1",
            "new_hint_id": "h2",
            "version": "2",
        })
    )
    client = AethisClient("ak_live_test", BASE)
    result = client.update_guidance("proj_1", "h1", "Updated text")
    assert result["new_hint_id"] == "h2"
    assert result["version"] == "2"


@respx.mock(base_url=BASE)
def test_add_guidance_with_source(respx_mock):
    """Client add_guidance should accept a source parameter."""
    route = respx_mock.post("/api/v1/public/projects/proj_1/guidance").mock(
        return_value=httpx.Response(201, json={"hint_id": "h1", "project_id": "proj_1"})
    )
    client = AethisClient("ak_live_test", BASE)
    client.add_guidance("proj_1", "Test hint", source="agent")

    # Verify the request body included source
    request = route.calls[0].request
    import json
    body = json.loads(request.content)
    assert body["source"] == "agent"
