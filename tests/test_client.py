"""Tests for AethisClient — all API methods against respx transport mocks."""

from __future__ import annotations

import pytest
import httpx
import respx

from aethis_cli.client import AethisClient
from aethis_cli.errors import AethisAPIError

BASE = "http://test.local"


# ============================================================================
# Decision API
# ============================================================================


@respx.mock(base_url=BASE)
def test_decide_eligible(respx_mock):
    respx_mock.post("/api/v1/public/decide").mock(
        return_value=httpx.Response(200, json={
            "decision": "eligible",
            "bundle_id": "test:123",
            "bundle_version": "v1",
            "fields_evaluated": 2,
            "fields_provided": 2,
        })
    )
    client = AethisClient("ak_live_test", BASE)
    result = client.decide("test:123", {"age": 25})
    assert result["decision"] == "eligible"


@respx.mock(base_url=BASE)
def test_decide_sends_api_key_header(respx_mock):
    route = respx_mock.post("/api/v1/public/decide").mock(
        return_value=httpx.Response(200, json={"decision": "eligible"})
    )
    AethisClient("ak_live_secret", BASE).decide("b:1", {})
    assert route.calls[0].request.headers["x-api-key"] == "ak_live_secret"


@respx.mock(base_url=BASE)
def test_decide_401_raises_api_error(respx_mock):
    respx_mock.post("/api/v1/public/decide").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid API key"})
    )
    with pytest.raises(AethisAPIError) as exc_info:
        AethisClient("bad", BASE).decide("b:1", {})
    assert exc_info.value.status_code == 401
    assert "Invalid API key" in exc_info.value.detail


@respx.mock(base_url=BASE)
def test_get_schema(respx_mock):
    respx_mock.get("/api/v1/public/bundles/b:1/schema").mock(
        return_value=httpx.Response(200, json={
            "bundle_id": "b:1",
            "fields": [{"field_id": "age", "field_type": "integer"}],
        })
    )
    result = AethisClient("ak", BASE).get_schema("b:1")
    assert len(result["fields"]) == 1
    assert result["fields"][0]["field_type"] == "integer"


@respx.mock(base_url=BASE)
def test_explain(respx_mock):
    respx_mock.get("/api/v1/public/bundles/b:1/explain").mock(
        return_value=httpx.Response(200, json={
            "bundle_id": "b:1",
            "criteria": [{"criterion_id": "c1", "title": "Age check", "rule_text": "age >= 18"}],
        })
    )
    result = AethisClient("ak", BASE).explain("b:1")
    assert result["criteria"][0]["title"] == "Age check"


# ============================================================================
# Projects API
# ============================================================================


@respx.mock(base_url=BASE)
def test_create_project(respx_mock):
    respx_mock.post("/api/v1/public/projects/").mock(
        return_value=httpx.Response(201, json={
            "project_id": "proj_abc",
            "name": "test",
            "section_id": "test_section",
            "status": "draft",
        })
    )
    result = AethisClient("ak", BASE).create_project("test", "test_section")
    assert result["project_id"] == "proj_abc"
    assert result["status"] == "draft"


@respx.mock(base_url=BASE)
def test_list_projects(respx_mock):
    respx_mock.get("/api/v1/public/projects/").mock(
        return_value=httpx.Response(200, json=[
            {"project_id": "proj_1", "name": "one"},
            {"project_id": "proj_2", "name": "two"},
        ])
    )
    result = AethisClient("ak", BASE).list_projects()
    assert len(result) == 2


@respx.mock(base_url=BASE)
def test_get_project(respx_mock):
    respx_mock.get("/api/v1/public/projects/proj_abc").mock(
        return_value=httpx.Response(200, json={"project_id": "proj_abc", "status": "ready"})
    )
    result = AethisClient("ak", BASE).get_project("proj_abc")
    assert result["status"] == "ready"


@respx.mock(base_url=BASE)
def test_add_guidance(respx_mock):
    respx_mock.post("/api/v1/public/projects/proj_abc/guidance").mock(
        return_value=httpx.Response(201, json={"hint_id": "hint_1", "project_id": "proj_abc"})
    )
    result = AethisClient("ak", BASE).add_guidance("proj_abc", "Age must be 18+")
    assert result["hint_id"] == "hint_1"


@respx.mock(base_url=BASE)
def test_upload_sources(respx_mock, tmp_path):
    respx_mock.post("/api/v1/public/projects/proj_abc/sources").mock(
        return_value=httpx.Response(201, json={
            "uploaded": 2,
            "sources": [
                {"source_id": "s1", "filename": "a.txt", "char_count": 10},
                {"source_id": "s2", "filename": "b.md", "char_count": 20},
            ],
        })
    )
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.md"
    f1.write_text("hello")
    f2.write_text("world")

    result = AethisClient("ak", BASE).upload_sources("proj_abc", [f1, f2])
    assert result["uploaded"] == 2


@respx.mock(base_url=BASE)
def test_add_tests(respx_mock):
    respx_mock.post("/api/v1/public/projects/proj_abc/tests").mock(
        return_value=httpx.Response(201, json={"added": 2, "test_case_ids": ["tc_1", "tc_2"]})
    )
    cases = [
        {"name": "eligible", "field_values": {"age": 25}, "expected_outcome": "eligible"},
        {"name": "not eligible", "field_values": {"age": 10}, "expected_outcome": "not_eligible"},
    ]
    result = AethisClient("ak", BASE).add_tests("proj_abc", cases)
    assert result["added"] == 2


@respx.mock(base_url=BASE)
def test_generate(respx_mock):
    respx_mock.post("/api/v1/public/projects/proj_abc/generate").mock(
        return_value=httpx.Response(202, json={"job_id": "job_1", "status": "queued"})
    )
    result = AethisClient("ak", BASE).generate("proj_abc")
    assert result["job_id"] == "job_1"
    assert result["status"] == "queued"


@respx.mock(base_url=BASE)
def test_get_status(respx_mock):
    respx_mock.get("/api/v1/public/projects/proj_abc/status").mock(
        return_value=httpx.Response(200, json={
            "project_status": "generating",
            "job": {"job_id": "job_1", "status": "running", "progress_percent": 50},
            "latest_bundle_id": None,
        })
    )
    result = AethisClient("ak", BASE).get_status("proj_abc")
    assert result["project_status"] == "generating"
    assert result["job"]["progress_percent"] == 50


@respx.mock(base_url=BASE)
def test_run_tests(respx_mock):
    respx_mock.post("/api/v1/public/projects/proj_abc/test-run").mock(
        return_value=httpx.Response(200, json={
            "total": 3, "passed": 2, "failed": 1, "errors": 0,
            "results": [
                {"tc_id": "tc_1", "name": "ok", "passed": True},
            ],
        })
    )
    result = AethisClient("ak", BASE).run_tests("proj_abc")
    assert result["passed"] == 2
    assert result["failed"] == 1


@respx.mock(base_url=BASE)
def test_publish(respx_mock):
    respx_mock.post("/api/v1/public/projects/proj_abc/publish").mock(
        return_value=httpx.Response(200, json={
            "message": "Bundle published", "bundle_id": "b:123", "project_id": "proj_abc",
        })
    )
    result = AethisClient("ak", BASE).publish("proj_abc")
    assert result["bundle_id"] == "b:123"


# ============================================================================
# Error handling
# ============================================================================


@respx.mock(base_url=BASE)
def test_404_raises_api_error(respx_mock):
    respx_mock.get("/api/v1/public/projects/proj_missing").mock(
        return_value=httpx.Response(404, json={"detail": "Project not found"})
    )
    with pytest.raises(AethisAPIError) as exc_info:
        AethisClient("ak", BASE).get_project("proj_missing")
    assert exc_info.value.status_code == 404


@respx.mock(base_url=BASE)
def test_429_raises_api_error(respx_mock):
    respx_mock.post("/api/v1/public/decide").mock(
        return_value=httpx.Response(429, json={"detail": "Rate limit exceeded"})
    )
    with pytest.raises(AethisAPIError) as exc_info:
        AethisClient("ak", BASE).decide("b:1", {})
    assert exc_info.value.status_code == 429


@respx.mock(base_url=BASE)
def test_non_json_error_body(respx_mock):
    respx_mock.post("/api/v1/public/decide").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with pytest.raises(AethisAPIError) as exc_info:
        AethisClient("ak", BASE).decide("b:1", {})
    assert exc_info.value.status_code == 500
