"""Acquire a real developer key the way a self-serve user does.

The staging integration lane must exercise the *same* path a new developer
walks: obtain a signed-in session for a fenced end-to-end user, then mint an
API key with the server's default scopes (no ``scopes`` field — Decision 3).
This module wraps that three-step dance so the tests read as intent, not
plumbing.

Steps (all bounded by timeouts, per the no-interactive-blocking rule):

1. Exchange the dev-tools secret for a one-time sign-in **ticket**
   (``POST api.clerk.com/v1/sign_in_tokens``).
2. Redeem the ticket for a session **JWT** at the Frontend API in browser
   mode (``POST clerk.aethis.ai/v1/client/sign_ins`` with a cookie jar and an
   ``Origin`` header — no native flag).
3. Mint / list / revoke keys against the staging keys API with that JWT.

Nothing here ever logs the secret, the ticket, the JWT, or a ``full_key`` —
they are treated as write-only. Callers that need a value hold it in a local
variable; it never reaches stdout/stderr.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import httpx

# Where the pieces live. The Frontend API host and the staging base URL are
# overridable for a self-hosted staging, but default to the real staging stack.
CLERK_FRONTEND_API = os.environ.get("CLERK_FRONTEND_API", "https://clerk.aethis.ai")
STAGING_BASE_URL = os.environ.get("AETHIS_BASE_URL", "https://staging.api.aethis.ai")
# The Origin the Frontend API expects for a browser-mode ticket redemption.
CLERK_ORIGIN = os.environ.get("CLERK_ORIGIN", "https://aethis.ai")

# Every key this lane mints carries this prefix so a crash-leaked key is
# identifiable and swept in teardown.
E2E_KEY_PREFIX = "e2e-dx-cli-"

_HTTP_TIMEOUT = 30.0


class StagingAuthError(RuntimeError):
    """A step in the sign-in / mint dance failed."""


@dataclass(frozen=True)
class MintedKey:
    """A freshly-minted key. ``full_key`` is the secret — never log it."""

    full_key: str
    key_id: str
    name: str
    scopes: list[str]
    rate_limit_tier: str


def creds_available() -> bool:
    """True when the Clerk dev-tools secret + e2e user id are both present."""
    return bool(os.environ.get("CLERK_SECRET_KEY_DEV_TOOLS") and os.environ.get("CLERK_E2E_DX_USER_ID"))


def missing_creds_reason() -> Optional[str]:
    """Human reason the creds are unusable, or ``None`` when they are present."""
    missing = [name for name in ("CLERK_SECRET_KEY_DEV_TOOLS", "CLERK_E2E_DX_USER_ID") if not os.environ.get(name)]
    if missing:
        return f"staging creds absent: {', '.join(missing)}"
    return None


def acquire_session_jwt() -> str:
    """Sign the fenced e2e user in and return a session JWT.

    Two hops: mint a one-time ticket with the backend secret, then redeem it
    at the Frontend API in browser mode (cookie jar + ``Origin`` header, no
    native flag) — the only combination the dev-tools instance accepts.
    """
    secret = os.environ.get("CLERK_SECRET_KEY_DEV_TOOLS")
    user_id = os.environ.get("CLERK_E2E_DX_USER_ID")
    if not secret or not user_id:
        raise StagingAuthError(missing_creds_reason() or "staging creds absent")

    try:
        ticket_resp = httpx.post(
            "https://api.clerk.com/v1/sign_in_tokens",
            headers={"Authorization": f"Bearer {secret}"},
            json={"user_id": user_id, "expires_in_seconds": 600},
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:  # network / DNS / TLS
        raise StagingAuthError(f"sign_in_tokens request failed: {exc}") from exc
    if ticket_resp.status_code != 200:
        raise StagingAuthError(f"sign_in_tokens returned HTTP {ticket_resp.status_code}")
    ticket = ticket_resp.json().get("token")
    if not ticket:
        raise StagingAuthError("sign_in_tokens response carried no token")

    try:
        with httpx.Client(cookies=httpx.Cookies(), timeout=_HTTP_TIMEOUT) as client:
            signin_resp = client.post(
                f"{CLERK_FRONTEND_API}/v1/client/sign_ins",
                headers={
                    "Origin": CLERK_ORIGIN,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"strategy": "ticket", "ticket": ticket},
            )
    except httpx.HTTPError as exc:
        raise StagingAuthError(f"sign_ins request failed: {exc}") from exc
    if signin_resp.status_code != 200:
        raise StagingAuthError(f"sign_ins returned HTTP {signin_resp.status_code}")

    try:
        jwt = signin_resp.json()["client"]["sessions"][0]["last_active_token"]["jwt"]
    except (KeyError, IndexError, TypeError) as exc:
        raise StagingAuthError("sign_ins response missing session JWT") from exc
    if not jwt:
        raise StagingAuthError("sign_ins response carried an empty JWT")
    return jwt


def _keys_client(jwt: str) -> httpx.Client:
    return httpx.Client(
        base_url=STAGING_BASE_URL,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=_HTTP_TIMEOUT,
    )


def mint_key(jwt: str, name: str, scopes: Optional[list[str]] = None) -> MintedKey:
    """Mint a key. Omit ``scopes`` to get the server default (Decision 3).

    ``scopes`` is only passed for the negative-path key (an explicit reduced
    set): the staging keys API exposes no scope-editing endpoint, so minting a
    second, deliberately-narrower key is the sanctioned way to prove the
    missing-permission path.
    """
    body: dict = {"name": name}
    if scopes is not None:
        body["scopes"] = scopes
    with _keys_client(jwt) as client:
        resp = client.post("/api/v1/keys/", json=body)
    if resp.status_code != 201:
        raise StagingAuthError(f"key mint returned HTTP {resp.status_code}")
    data = resp.json()
    return MintedKey(
        full_key=data["full_key"],
        key_id=data["key_id"],
        name=data["name"],
        scopes=data["scopes"],
        rate_limit_tier=data["rate_limit_tier"],
    )


def revoke_key(jwt: str, key_id: str) -> None:
    """Revoke a key by id. A 204 or an already-gone 404 both count as done."""
    with _keys_client(jwt) as client:
        resp = client.delete(f"/api/v1/keys/{key_id}")
    if resp.status_code not in (204, 404):
        raise StagingAuthError(f"key revoke returned HTTP {resp.status_code}")


def list_live_e2e_key_ids(jwt: str) -> list[str]:
    """Return the ids of every non-revoked ``e2e-dx-cli-*`` key on the tenant.

    Used by teardown to sweep keys a crashed run may have leaked, so the
    fenced user never accumulates orphaned credentials.
    """
    with _keys_client(jwt) as client:
        resp = client.get("/api/v1/keys/")
    if resp.status_code != 200:
        raise StagingAuthError(f"key list returned HTTP {resp.status_code}")
    return [
        k["key_id"] for k in resp.json() if str(k.get("name", "")).startswith(E2E_KEY_PREFIX) and not k.get("revoked")
    ]


def sweep_e2e_keys(jwt: str) -> int:
    """Revoke every stray ``e2e-dx-cli-*`` key; return how many were swept."""
    stray = list_live_e2e_key_ids(jwt)
    for key_id in stray:
        try:
            revoke_key(jwt, key_id)
        except StagingAuthError:
            # Best-effort cleanup — one failure must not abort the sweep.
            pass
    return len(stray)
