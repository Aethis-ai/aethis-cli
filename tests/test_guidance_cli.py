"""TDD tests for guidance CLI commands and client methods.

Tests written FIRST, then implement to make them pass.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from aethis_cli.client import AethisClient

BASE = "https://test.local"


# ============================================================================
# generate_cmd hints.yaml parsing (string vs dict format)
# ============================================================================


def _setup_generate_mocks(mock_router, project_id: str = "proj_1") -> object:
    """Register all routes needed for a generate --no-poll run. Returns the guidance route."""
    guidance_route = mock_router.post(f"/api/v1/public/projects/{project_id}/guidance").mock(
        return_value=httpx.Response(201, json={"hint_id": "h1"})
    )
    mock_router.get(f"/api/v1/public/projects/{project_id}").mock(
        return_value=httpx.Response(200, json={"project_id": project_id})
    )
    mock_router.post(f"/api/v1/public/projects/{project_id}/generate").mock(
        return_value=httpx.Response(202, json={"job_id": "j1"})
    )
    return guidance_route


def test_generate_cmd_string_hints_passed_with_default_process_type(tmp_path, monkeypatch):
    """Plain-string hints in hints.yaml should be sent with process_type=rule_generation."""
    (tmp_path / "aethis.yaml").write_text(
        "project: test\napi_key_env: AETHIS_KEY\nbase_url: https://test.local\n"
    )
    monkeypatch.setenv("AETHIS_KEY", "ak_test")
    (tmp_path / "sources").mkdir()
    (tmp_path / "guidance").mkdir()
    (tmp_path / "guidance" / "hints.yaml").write_text(
        "hints:\n  - Plain string hint\n"
    )
    (tmp_path / ".aethis").mkdir()
    (tmp_path / ".aethis" / "state.json").write_text(json.dumps({"project_id": "proj_1"}))
    monkeypatch.chdir(tmp_path)

    with respx.mock(base_url="https://test.local", assert_all_called=False) as mock_router:
        guidance_route = _setup_generate_mocks(mock_router)

        from typer.testing import CliRunner
        from aethis_cli.main import app
        runner = CliRunner()
        result = runner.invoke(app, ["generate", "--no-poll"])
        assert result.exit_code == 0, result.output

        body = json.loads(guidance_route.calls[0].request.content)
        assert body["process_type"] == "rule_generation"
        assert body["guidance_text"] == "Plain string hint"


def test_generate_cmd_dict_hints_pass_process_type(tmp_path, monkeypatch):
    """Dict-format hints in hints.yaml should pass their process_type to add_guidance."""
    (tmp_path / "aethis.yaml").write_text(
        "project: test\napi_key_env: AETHIS_KEY\nbase_url: https://test.local\n"
    )
    monkeypatch.setenv("AETHIS_KEY", "ak_test")
    (tmp_path / "sources").mkdir()
    (tmp_path / "guidance").mkdir()
    (tmp_path / "guidance" / "hints.yaml").write_text(
        "hints:\n"
        "  - text: Extract the date of birth\n"
        "    process_type: field_extraction\n"
        "    notes: Core to Form AN\n"
        "  - text: Age must exceed 18\n"
    )
    (tmp_path / ".aethis").mkdir()
    (tmp_path / ".aethis" / "state.json").write_text(json.dumps({"project_id": "proj_1"}))
    monkeypatch.chdir(tmp_path)

    with respx.mock(base_url="https://test.local", assert_all_called=False) as mock_router:
        guidance_route = _setup_generate_mocks(mock_router)

        from typer.testing import CliRunner
        from aethis_cli.main import app
        runner = CliRunner()
        result = runner.invoke(app, ["generate", "--no-poll"])
        assert result.exit_code == 0, result.output

        assert len(guidance_route.calls) == 2

        body0 = json.loads(guidance_route.calls[0].request.content)
        assert body0["guidance_text"] == "Extract the date of birth"
        assert body0["process_type"] == "field_extraction"

        body1 = json.loads(guidance_route.calls[1].request.content)
        assert body1["guidance_text"] == "Age must exceed 18"
        assert body1["process_type"] == "rule_generation"


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


@respx.mock(base_url=BASE)
def test_add_guidance_with_process_type(respx_mock):
    """Client add_guidance should send process_type in the request body."""
    route = respx_mock.post("/api/v1/public/projects/proj_1/guidance").mock(
        return_value=httpx.Response(201, json={"hint_id": "h1", "project_id": "proj_1"})
    )
    client = AethisClient("ak_live_test", BASE)
    client.add_guidance("proj_1", "Extract the applicant's date of birth", process_type="field_extraction")

    import json
    body = json.loads(route.calls[0].request.content)
    assert body["process_type"] == "field_extraction"
    assert body["guidance_text"] == "Extract the applicant's date of birth"


@respx.mock(base_url=BASE)
def test_add_guidance_default_process_type(respx_mock):
    """Client add_guidance should default process_type to rule_generation."""
    route = respx_mock.post("/api/v1/public/projects/proj_1/guidance").mock(
        return_value=httpx.Response(201, json={"hint_id": "h2", "project_id": "proj_1"})
    )
    client = AethisClient("ak_live_test", BASE)
    client.add_guidance("proj_1", "Applicant must be over 18")

    import json
    body = json.loads(route.calls[0].request.content)
    assert body["process_type"] == "rule_generation"


@respx.mock(base_url=BASE)
def test_add_domain_guidance(respx_mock):
    """Client should post to domain guidance endpoint with correct body."""
    route = respx_mock.post("/api/v1/public/domains/uk_citizenship/guidance").mock(
        return_value=httpx.Response(201, json={"hint_id": "dh1", "domain": "uk_citizenship"})
    )
    client = AethisClient("ak_live_test", BASE)
    result = client.add_domain_guidance(
        "uk_citizenship",
        "Aethis never exercises discretion",
        process_type="rule_generation",
        notes="Core principle",
    )

    assert result["hint_id"] == "dh1"

    import json
    body = json.loads(route.calls[0].request.content)
    assert body["guidance_text"] == "Aethis never exercises discretion"
    assert body["process_type"] == "rule_generation"
    assert body["notes"] == "Core principle"


@respx.mock(base_url=BASE)
def test_add_domain_guidance_no_notes(respx_mock):
    """Client should omit notes key when not provided."""
    route = respx_mock.post("/api/v1/public/domains/uk_citizenship/guidance").mock(
        return_value=httpx.Response(201, json={"hint_id": "dh2"})
    )
    client = AethisClient("ak_live_test", BASE)
    client.add_domain_guidance("uk_citizenship", "Some principle")

    import json
    body = json.loads(route.calls[0].request.content)
    assert "notes" not in body


@respx.mock(base_url=BASE)
def test_list_domain_guidance(respx_mock):
    """Client should GET domain guidance and return a list."""
    respx_mock.get("/api/v1/public/domains/uk_citizenship/guidance").mock(
        return_value=httpx.Response(200, json=[
            {"hint_id": "dh1", "guidance_text": "Principle A", "process_type": "rule_generation"},
            {"hint_id": "dh2", "guidance_text": "Principle B", "process_type": "field_extraction"},
        ])
    )
    client = AethisClient("ak_live_test", BASE)
    result = client.list_domain_guidance("uk_citizenship")
    assert len(result) == 2
    assert result[1]["process_type"] == "field_extraction"


# ============================================================================
# A1 CLI — import dedup skip count
# ============================================================================


def test_guidance_import_reports_skipped(tmp_path, monkeypatch):
    """guidance import should report N skipped when server returns skipped:true."""
    (tmp_path / "aethis.yaml").write_text(
        "project: test\napi_key_env: AETHIS_KEY\nbase_url: https://test.local\n"
    )
    monkeypatch.setenv("AETHIS_KEY", "ak_test")
    (tmp_path / ".aethis").mkdir()
    (tmp_path / ".aethis" / "state.json").write_text(json.dumps({"project_id": "proj_skip"}))
    monkeypatch.chdir(tmp_path)

    hints_file = tmp_path / "hints.yaml"
    hints_file.write_text("hints:\n  - Existing hint\n  - New hint\n")

    with respx.mock(base_url="https://test.local", assert_all_called=False) as mock_router:
        # First hint already exists (skipped), second is new
        mock_router.post("/api/v1/public/projects/proj_skip/guidance").mock(
            side_effect=[
                httpx.Response(201, json={"hint_id": "h1", "skipped": True}),
                httpx.Response(201, json={"hint_id": "h2", "skipped": False}),
            ]
        )

        from typer.testing import CliRunner
        from aethis_cli.main import app
        runner = CliRunner()
        result = runner.invoke(app, ["guidance", "import", str(hints_file)])
        assert result.exit_code == 0, result.output
        assert "Imported 1 hint(s)" in result.output
        assert "1 skipped (already exist)" in result.output


def test_guidance_import_no_skips(tmp_path, monkeypatch):
    """guidance import with no duplicates should not mention skipped."""
    (tmp_path / "aethis.yaml").write_text(
        "project: test\napi_key_env: AETHIS_KEY\nbase_url: https://test.local\n"
    )
    monkeypatch.setenv("AETHIS_KEY", "ak_test")
    (tmp_path / ".aethis").mkdir()
    (tmp_path / ".aethis" / "state.json").write_text(json.dumps({"project_id": "proj_noskip"}))
    monkeypatch.chdir(tmp_path)

    hints_file = tmp_path / "hints.yaml"
    hints_file.write_text("hints:\n  - Brand new hint\n")

    with respx.mock(base_url="https://test.local", assert_all_called=False) as mock_router:
        mock_router.post("/api/v1/public/projects/proj_noskip/guidance").mock(
            return_value=httpx.Response(201, json={"hint_id": "h3", "skipped": False})
        )

        from typer.testing import CliRunner
        from aethis_cli.main import app
        runner = CliRunner()
        result = runner.invoke(app, ["guidance", "import", str(hints_file)])
        assert result.exit_code == 0, result.output
        assert "Imported 1 hint(s)" in result.output
        assert "skipped" not in result.output


def test_domain_guidance_import_reports_skipped(tmp_path, monkeypatch):
    """domain guidance import should report N skipped when server returns skipped:true."""
    (tmp_path / "aethis.yaml").write_text(
        "project: test\napi_key_env: AETHIS_KEY\nbase_url: https://test.local\n"
    )
    monkeypatch.setenv("AETHIS_KEY", "ak_test")
    monkeypatch.chdir(tmp_path)

    hints_file = tmp_path / "domain_hints.yaml"
    hints_file.write_text(
        "domain: uk_citizenship\nhints:\n  - Already exists\n  - Also exists\n  - This is new\n"
    )

    with respx.mock(base_url="https://test.local", assert_all_called=False) as mock_router:
        mock_router.post("/api/v1/public/domains/uk_citizenship/guidance").mock(
            side_effect=[
                httpx.Response(201, json={"hint_id": "dh1", "skipped": True}),
                httpx.Response(201, json={"hint_id": "dh2", "skipped": True}),
                httpx.Response(201, json={"hint_id": "dh3", "skipped": False}),
            ]
        )

        from typer.testing import CliRunner
        from aethis_cli.main import app
        runner = CliRunner()
        result = runner.invoke(app, ["domain", "guidance", "import", "uk_citizenship", str(hints_file)])
        assert result.exit_code == 0, result.output
        assert "Imported 1 hint(s)" in result.output
        assert "2 skipped (already exist)" in result.output


def test_domain_guidance_import_all_skipped(tmp_path, monkeypatch):
    """domain guidance import where all hints exist should report 0 imported, N skipped."""
    (tmp_path / "aethis.yaml").write_text(
        "project: test\napi_key_env: AETHIS_KEY\nbase_url: https://test.local\n"
    )
    monkeypatch.setenv("AETHIS_KEY", "ak_test")
    monkeypatch.chdir(tmp_path)

    hints_file = tmp_path / "domain_hints.yaml"
    hints_file.write_text("domain: uk_citizenship\nhints:\n  - Duplicate hint\n")

    with respx.mock(base_url="https://test.local", assert_all_called=False) as mock_router:
        mock_router.post("/api/v1/public/domains/uk_citizenship/guidance").mock(
            return_value=httpx.Response(201, json={"hint_id": "dh1", "skipped": True})
        )

        from typer.testing import CliRunner
        from aethis_cli.main import app
        runner = CliRunner()
        result = runner.invoke(app, ["domain", "guidance", "import", "uk_citizenship", str(hints_file)])
        assert result.exit_code == 0, result.output
        assert "Imported 0 hint(s)" in result.output
        assert "1 skipped (already exist)" in result.output
