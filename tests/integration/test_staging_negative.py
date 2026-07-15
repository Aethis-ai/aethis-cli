"""Staging negative paths — what the user actually sees when denied.

Two failure modes, asserted both at the wire (the error *envelope* conforms to
the P1 contract) and at the CLI (the rendered text is *readable* — it names the
missing scope and shows the server's hint, rather than dumping a raw dict):

- **403** — a key minted without any ``projects`` scope calls ``projects
  list``; the server returns ``denied_missing_permission`` and the CLI renders
  the missing permission + hint.
- **401** — a revoked key calls ``whoami``; the server returns
  ``invalid_api_key`` and the CLI renders a clean re-authenticate line.

The scope-stripped key is minted with an explicit reduced scope set rather than
edited down after the fact: the staging keys API has no scope-editing endpoint,
so a second, narrower key is the sanctioned equivalent.
"""

from __future__ import annotations

import pytest

from aethis_cli.client import AethisClient
from aethis_cli.errors import AethisAPIError

from . import staging_auth
from .cli_driver import run_cli as _run_cli
from .contract import envelope_violations

pytestmark = pytest.mark.staging


# --------------------------------------------------------------------------- #
# 403 — missing scope
# --------------------------------------------------------------------------- #


def test_missing_scope_envelope_matches_contract(reduced_scope_key, staging_base_url, contract):
    """The wire-level 403 envelope conforms to the contract's 403 schema."""
    client = AethisClient(reduced_scope_key.full_key, staging_base_url)
    with pytest.raises(AethisAPIError) as excinfo:
        client.list_projects()
    err = excinfo.value
    assert err.status_code == 403
    violations = envelope_violations(err.detail, contract["envelopes"]["403"])
    assert not violations, f"403 envelope violates contract: {violations}"
    assert "projects:read" in err.detail.get("missing_permissions", [])


def test_missing_scope_rendered_readably(reduced_scope_key, staging_base_url):
    """The CLI renders the missing permission and the server hint, not a dict."""
    r = _run_cli(["projects", "list"], staging_base_url, api_key=reduced_scope_key.full_key)
    assert r.returncode == 1
    combined = r.stdout + r.stderr
    # Readable: names the missing scope and the reason, not a Python dict repr.
    assert "missing=projects:read" in combined
    assert "denied_missing_permission" in combined
    assert "{'error'" not in combined, "error rendered as a raw dict"
    # The server's hint (how to request access) is surfaced on its own line.
    assert "invite-only" in combined or "sign-up" in combined


# --------------------------------------------------------------------------- #
# 401 — revoked key
# --------------------------------------------------------------------------- #


def test_revoked_key_envelope_matches_contract(session_jwt, staging_base_url, contract):
    """A revoked key's 401 envelope conforms to the contract's 401 schema."""
    key = staging_auth.mint_key(session_jwt, name=f"{staging_auth.E2E_KEY_PREFIX}revoke-probe")
    staging_auth.revoke_key(session_jwt, key.key_id)
    client = AethisClient(key.full_key, staging_base_url)
    with pytest.raises(AethisAPIError) as excinfo:
        client.whoami()
    err = excinfo.value
    assert err.status_code == 401
    violations = envelope_violations(err.detail, contract["envelopes"]["401"])
    assert not violations, f"401 envelope violates contract: {violations}"
    assert err.detail.get("reason_code") == "invalid_api_key"


def test_revoked_key_rendered_readably(session_jwt, staging_base_url):
    """The CLI renders a clean 401 line + a re-authenticate hint."""
    key = staging_auth.mint_key(session_jwt, name=f"{staging_auth.E2E_KEY_PREFIX}revoke-render")
    staging_auth.revoke_key(session_jwt, key.key_id)
    r = _run_cli(["whoami"], staging_base_url, api_key=key.full_key)
    assert r.returncode == 1
    combined = (r.stdout + r.stderr).lower()
    assert "invalid" in combined or "revoked" in combined
    assert "aethis login" in combined
