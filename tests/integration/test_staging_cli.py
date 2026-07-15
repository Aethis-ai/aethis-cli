"""Staging happy path — drive the real CLI with a freshly-minted key.

Acquires a key the self-serve way (``default_key`` fixture) and walks the core
developer loop end-to-end against deployed staging: identity (``whoami`` /
``status``), the projects lifecycle (create via the API, then ``list`` and
``archive`` through the CLI), and the read/decision surface (``rulesets``,
``explain``, ``fields``, ``decide``) against a public showcase ruleset — never
any immigration / Form AN content.

The CLI is driven as a subprocess (the surface a user actually types); the
project is created through :class:`AethisClient` because there is no
``projects create`` command, only ``list`` / ``show`` / ``archive``.
"""

from __future__ import annotations

import json

import pytest

from aethis_cli.client import AethisClient

from . import staging_auth
from .cli_driver import run_cli as _run_cli

pytestmark = pytest.mark.staging


@pytest.fixture(scope="session")
def showcase_ruleset(default_key: staging_auth.MintedKey, staging_base_url: str) -> str:
    """A public showcase ruleset id to exercise read/decide against.

    Prefers the spacecraft ruleset (known non-immigration showcase); falls back
    to the first public ruleset the catalogue returns.
    """
    client = AethisClient(default_key.full_key, staging_base_url)
    rulesets = client.list_public_rulesets(limit=50)
    if not rulesets:
        pytest.skip("no public rulesets on staging to decide against")
    for rs in rulesets:
        slug = (rs.get("slug") or "").lower()
        if "spacecraft" in slug:
            return rs["ruleset_id"]
    return rulesets[0]["ruleset_id"]


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def test_whoami_reports_minted_key(default_key, staging_base_url):
    r = _run_cli(["whoami"], staging_base_url, api_key=default_key.full_key)
    assert r.returncode == 0, r.stderr
    assert default_key.key_id in r.stdout


def test_status_json_carries_identity(default_key, staging_base_url):
    r = _run_cli(["status"], staging_base_url, api_key=default_key.full_key, json_output=True)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["identity"]["key_id"] == default_key.key_id


# --------------------------------------------------------------------------- #
# Projects lifecycle: create (API) → list (CLI) → archive (CLI)
# --------------------------------------------------------------------------- #


def test_projects_list_and_archive(default_key, staging_base_url):
    client = AethisClient(default_key.full_key, staging_base_url)
    project = client.create_project(
        name=staging_auth.E2E_PROJECT_PREFIX,
        section_id="e2e_dx_section",
        domain="e2e_dx",
    )
    pid = project["project_id"]
    try:
        listed = _run_cli(["projects", "list"], staging_base_url, api_key=default_key.full_key, json_output=True)
        assert listed.returncode == 0, listed.stderr
        ids = {p["project_id"] for p in json.loads(listed.stdout)}
        assert pid in ids, f"created project {pid} not in CLI list"
    finally:
        archived = _run_cli(["projects", "archive", pid, "--yes"], staging_base_url, api_key=default_key.full_key)
        assert archived.returncode == 0, archived.stderr
        assert "archived" in archived.stdout.lower()


# --------------------------------------------------------------------------- #
# Read + decision surface against a public showcase ruleset
# --------------------------------------------------------------------------- #


def test_rulesets_list_public(default_key, staging_base_url):
    r = _run_cli(["rulesets", "list"], staging_base_url, api_key=default_key.full_key, json_output=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout), "expected at least one public ruleset"


def test_explain_and_fields(showcase_ruleset, default_key, staging_base_url):
    explain = _run_cli(["explain", "-b", showcase_ruleset], staging_base_url, api_key=default_key.full_key)
    assert explain.returncode == 0, explain.stderr
    fields = _run_cli(["fields", "-b", showcase_ruleset], staging_base_url, api_key=default_key.full_key)
    assert fields.returncode == 0, fields.stderr


def test_decide_round_trips(showcase_ruleset, default_key, staging_base_url):
    r = _run_cli(
        ["decide", "-b", showcase_ruleset, "-i", "{}"],
        staging_base_url,
        api_key=default_key.full_key,
        json_output=True,
    )
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result.get("decision") in {"eligible", "not_eligible", "undetermined", "unknown"}
    assert result.get("ruleset_id")
