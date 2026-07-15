"""Fixtures for the staging integration lane (marker ``staging``).

Session lifecycle:

- Sign the fenced e2e user in once (``session_jwt``).
- Mint one default-scope key for the happy path (``default_key``) and one
  deliberately reduced-scope key for the negative path (``reduced_scope_key``);
  revoke both in teardown.
- Sweep any stray ``e2e-dx-cli-*`` keys a previous crashed run may have leaked,
  so the fenced user never accumulates orphaned credentials.

Absent creds: the tests **skip** so a developer without the Clerk dev-tools
secret isn't blocked. The *CI workflow* fails loud on a missing secret instead
(a CI lane must report red, never silently pass) — the skip is a local-dev
ergonomic, not a CI escape hatch.
"""

from __future__ import annotations

import os
from typing import Iterator

import pytest

from aethis_cli.client import AethisClient

from . import staging_auth
from .contract import ContractUnavailable, is_strict, load_contract


@pytest.fixture(scope="session")
def staging_base_url() -> str:
    return staging_auth.STAGING_BASE_URL


@pytest.fixture(scope="session")
def session_jwt() -> Iterator[str]:
    """A session JWT for the fenced e2e user; sweeps stray keys on teardown."""
    reason = staging_auth.missing_creds_reason()
    if reason:
        pytest.skip(reason)
    try:
        jwt = staging_auth.acquire_session_jwt()
    except staging_auth.StagingAuthError as exc:
        pytest.fail(f"could not acquire staging session: {exc}")
    yield jwt
    # Best-effort teardown sweep — never let cleanup failure mask a test result.
    try:
        staging_auth.sweep_e2e_keys(jwt)
    except staging_auth.StagingAuthError:
        pass


def _run_id() -> str:
    """A short per-run tag so concurrent lanes don't collide on key names."""
    return os.environ.get("GITHUB_RUN_ID") or os.environ.get("USER", "local")


@pytest.fixture(scope="session")
def default_key(session_jwt: str) -> Iterator[staging_auth.MintedKey]:
    """A key minted with the server's default scopes (no ``scopes`` field)."""
    key = staging_auth.mint_key(session_jwt, name=f"{staging_auth.E2E_KEY_PREFIX}{_run_id()}")
    yield key
    staging_auth.revoke_key(session_jwt, key.key_id)


@pytest.fixture
def reduced_scope_key(session_jwt: str) -> Iterator[staging_auth.MintedKey]:
    """A key minted WITHOUT any projects scope, to drive the 403 path.

    ``projects:read`` is granted transitively by ``projects:write`` (scope
    alias), so both must be absent for ``projects list`` to be denied.
    """
    key = staging_auth.mint_key(
        session_jwt,
        name=f"{staging_auth.E2E_KEY_PREFIX}{_run_id()}-reduced",
        scopes=["decide", "rulesets:read", "rulesets:explain"],
    )
    yield key
    staging_auth.revoke_key(session_jwt, key.key_id)


@pytest.fixture(scope="session")
def contract() -> dict:
    """The public-API contract; RED in CI if unreachable, skip locally."""
    try:
        return load_contract()
    except ContractUnavailable as exc:
        if is_strict():
            pytest.fail(f"contract unavailable in strict/CI mode (a CI lane must report red, not skip): {exc}")
        pytest.skip(f"contract unavailable (non-strict local run): {exc}")


def client_for(key: staging_auth.MintedKey, base_url: str) -> AethisClient:
    """Build an ``AethisClient`` bound to a minted key (used by the happy path)."""
    return AethisClient(key.full_key, base_url)
