"""aethis iam — CLI-first IAM management commands."""

from __future__ import annotations

import os
from typing import Optional

import httpx
import typer

from aethis_cli.auth import authenticate_with_clerk
from aethis_cli.config import DEFAULT_BASE_URL
from aethis_cli.errors import AuthenticationError
from aethis_cli.output import console, info, success

_BASE_URL = os.environ.get("AETHIS_BASE_URL", DEFAULT_BASE_URL)
CLERK_DOMAIN = os.environ.get("AETHIS_CLERK_DOMAIN", "clerk.aethis.legal")
CLERK_CLIENT_ID = os.environ.get("AETHIS_CLERK_CLIENT_ID", "cwH009p1vPtyy1EG")

iam_app = typer.Typer(name="iam", help="Manage IAM users/groups/relationships.", no_args_is_help=True)


def _clerk_auth(timeout: int) -> str:
    access_token = os.environ.get("AETHIS_ACCESS_TOKEN")
    if access_token:
        return access_token
    try:
        return authenticate_with_clerk(CLERK_DOMAIN, CLERK_CLIENT_ID, timeout)
    except AuthenticationError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None


def _format_api_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return resp.text
    detail = data.get("detail", data) if isinstance(data, dict) else data
    if isinstance(detail, dict):
        reason = detail.get("reason_code", "unknown")
        action = detail.get("action", "unknown")
        missing = detail.get("missing_permissions", [])
        missing_str = ", ".join(missing) if isinstance(missing, list) else str(missing)
        message = detail.get("message") or detail.get("error") or "Request denied"
        return f"{message} (reason={reason}, action={action}, missing={missing_str})"
    return str(detail)


def _request(method: str, path: str, access_token: str, base_url: str, **kwargs):
    try:
        resp = httpx.request(
            method,
            f"{base_url}{path}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
            **kwargs,
        )
    except httpx.HTTPError as e:
        console.print(f"[red]Could not reach API at {base_url}: {e}[/red]")
        raise typer.Exit(code=1) from None
    if resp.status_code >= 400:
        console.print(f"[red]Request failed (HTTP {resp.status_code}): {_format_api_error(resp)}[/red]")
        raise typer.Exit(code=1)
    return resp


@iam_app.command("users-list")
def users_list(
    org_id: str = typer.Option(..., "--org-id"),
    base_url: str = typer.Option(_BASE_URL, "--base-url"),
    timeout: int = typer.Option(120, "--timeout"),
) -> None:
    token = _clerk_auth(timeout)
    resp = _request("GET", "/api/v1/iam/users", token, base_url, params={"org_id": org_id})
    users = resp.json().get("users", [])
    for user in users:
        console.print(user)


@iam_app.command("grant-role")
def grant_role(
    user_id: str = typer.Argument(...),
    org_id: str = typer.Option(..., "--org-id"),
    domain: str = typer.Option(..., "--domain"),
    role: str = typer.Option(..., "--role"),
    base_url: str = typer.Option(_BASE_URL, "--base-url"),
    timeout: int = typer.Option(120, "--timeout"),
) -> None:
    token = _clerk_auth(timeout)
    _request(
        "POST",
        f"/api/v1/iam/users/{user_id}/roles",
        token,
        base_url,
        json={"org_id": org_id, "domain": domain, "role": role},
    )
    success("Role granted.")


@iam_app.command("revoke-role")
def revoke_role(
    user_id: str = typer.Argument(...),
    role: str = typer.Argument(...),
    org_id: str = typer.Option(..., "--org-id"),
    domain: str = typer.Option(..., "--domain"),
    base_url: str = typer.Option(_BASE_URL, "--base-url"),
    timeout: int = typer.Option(120, "--timeout"),
) -> None:
    token = _clerk_auth(timeout)
    _request(
        "DELETE",
        f"/api/v1/iam/users/{user_id}/roles/{role}",
        token,
        base_url,
        params={"org_id": org_id, "domain": domain},
    )
    success("Role revoked.")


@iam_app.command("groups-list")
def groups_list(
    org_id: str = typer.Option(..., "--org-id"),
    base_url: str = typer.Option(_BASE_URL, "--base-url"),
    timeout: int = typer.Option(120, "--timeout"),
) -> None:
    token = _clerk_auth(timeout)
    resp = _request("GET", "/api/v1/iam/groups", token, base_url, params={"org_id": org_id})
    for group in resp.json().get("groups", []):
        console.print(f"{group['group_id']}\t{group['name']}")


@iam_app.command("groups-create")
def groups_create(
    group_id: str = typer.Argument(...),
    name: str = typer.Option(..., "--name"),
    org_id: str = typer.Option(..., "--org-id"),
    base_url: str = typer.Option(_BASE_URL, "--base-url"),
    timeout: int = typer.Option(120, "--timeout"),
) -> None:
    token = _clerk_auth(timeout)
    _request("POST", "/api/v1/iam/groups", token, base_url, json={"org_id": org_id, "group_id": group_id, "name": name})
    success("Group created.")


@iam_app.command("groups-delete")
def groups_delete(
    group_id: str = typer.Argument(...),
    org_id: str = typer.Option(..., "--org-id"),
    base_url: str = typer.Option(_BASE_URL, "--base-url"),
    timeout: int = typer.Option(120, "--timeout"),
) -> None:
    token = _clerk_auth(timeout)
    _request("DELETE", f"/api/v1/iam/groups/{group_id}", token, base_url, params={"org_id": org_id})
    success("Group deleted.")


@iam_app.command("groups-add-user")
def groups_add_user(
    group_id: str = typer.Argument(...),
    user_id: str = typer.Argument(...),
    org_id: str = typer.Option(..., "--org-id"),
    base_url: str = typer.Option(_BASE_URL, "--base-url"),
    timeout: int = typer.Option(120, "--timeout"),
) -> None:
    token = _clerk_auth(timeout)
    _request("POST", f"/api/v1/iam/groups/{group_id}/members/{user_id}", token, base_url, params={"org_id": org_id})
    success("User added to group.")


@iam_app.command("groups-remove-user")
def groups_remove_user(
    group_id: str = typer.Argument(...),
    user_id: str = typer.Argument(...),
    org_id: str = typer.Option(..., "--org-id"),
    base_url: str = typer.Option(_BASE_URL, "--base-url"),
    timeout: int = typer.Option(120, "--timeout"),
) -> None:
    token = _clerk_auth(timeout)
    _request("DELETE", f"/api/v1/iam/groups/{group_id}/members/{user_id}", token, base_url, params={"org_id": org_id})
    success("User removed from group.")


@iam_app.command("relationships-list")
def relationships_list(
    org_id: str = typer.Option(..., "--org-id"),
    domain: Optional[str] = typer.Option(None, "--domain"),
    subject_id: Optional[str] = typer.Option(None, "--subject-id"),
    base_url: str = typer.Option(_BASE_URL, "--base-url"),
    timeout: int = typer.Option(120, "--timeout"),
) -> None:
    token = _clerk_auth(timeout)
    params = {"org_id": org_id}
    if domain:
        params["domain"] = domain
    if subject_id:
        params["subject_id"] = subject_id
    resp = _request("GET", "/api/v1/iam/relationships", token, base_url, params=params)
    for rel in resp.json().get("relationships", []):
        console.print(
            f"{rel['subject_id']} {rel['relation']} {rel['object_type']}:{rel['object_id']} ({rel['domain']})"
        )


@iam_app.command("relationships-add")
def relationships_add(
    org_id: str = typer.Option(..., "--org-id"),
    domain: str = typer.Option(..., "--domain"),
    subject_id: str = typer.Option(..., "--subject-id"),
    relation: str = typer.Option(..., "--relation"),
    object_type: str = typer.Option(..., "--object-type"),
    object_id: str = typer.Option(..., "--object-id"),
    base_url: str = typer.Option(_BASE_URL, "--base-url"),
    timeout: int = typer.Option(120, "--timeout"),
) -> None:
    token = _clerk_auth(timeout)
    _request(
        "POST",
        "/api/v1/iam/relationships",
        token,
        base_url,
        json={
            "org_id": org_id,
            "domain": domain,
            "subject_id": subject_id,
            "relation": relation,
            "object_type": object_type,
            "object_id": object_id,
        },
    )
    success("Relationship added.")


@iam_app.command("relationships-remove")
def relationships_remove(
    org_id: str = typer.Option(..., "--org-id"),
    domain: str = typer.Option(..., "--domain"),
    subject_id: str = typer.Option(..., "--subject-id"),
    relation: str = typer.Option(..., "--relation"),
    object_type: str = typer.Option(..., "--object-type"),
    object_id: str = typer.Option(..., "--object-id"),
    base_url: str = typer.Option(_BASE_URL, "--base-url"),
    timeout: int = typer.Option(120, "--timeout"),
) -> None:
    token = _clerk_auth(timeout)
    _request(
        "DELETE",
        "/api/v1/iam/relationships",
        token,
        base_url,
        params={
            "org_id": org_id,
            "domain": domain,
            "subject_id": subject_id,
            "relation": relation,
            "object_type": object_type,
            "object_id": object_id,
        },
    )
    success("Relationship removed.")


@iam_app.command("audit")
def audit(
    org_id: str = typer.Option(..., "--org-id"),
    event_type: Optional[str] = typer.Option(None, "--event-type"),
    actor_id: Optional[str] = typer.Option(None, "--actor-id"),
    base_url: str = typer.Option(_BASE_URL, "--base-url"),
    timeout: int = typer.Option(120, "--timeout"),
) -> None:
    token = _clerk_auth(timeout)
    params = {"org_id": org_id}
    if event_type:
        params["event_type"] = event_type
    if actor_id:
        params["actor_id"] = actor_id
    resp = _request("GET", "/api/v1/iam/audit", token, base_url, params=params)
    events = resp.json().get("events", [])
    info(f"{len(events)} events")
    for event in events:
        console.print(
            f"{event.get('created_at', '')} {event['event_type']} {event['domain']} {event.get('resource_id', '')}"
        )
