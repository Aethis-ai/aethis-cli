from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from typer.testing import CliRunner

from aethis_cli.main import app


runner = CliRunner()


class _IAMHandler(BaseHTTPRequestHandler):
    routes: dict[tuple[str, str], tuple[int, dict]] = {}

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        path = self.path.split("?", 1)[0]
        code, payload = self.routes.get((self.command, path), (404, {"detail": "not found"}))
        self._send(code, payload)

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle()

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        return


def _start_server(routes: dict[tuple[str, str], tuple[int, dict]]):
    _IAMHandler.routes = routes
    server = HTTPServer(("127.0.0.1", 0), _IAMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_iam_users_list_and_groups_create(monkeypatch) -> None:
    routes = {
        ("GET", "/api/v1/iam/users"): (200, {"users": ["u_1", "u_2"]}),
        ("POST", "/api/v1/iam/groups"): (201, {"ok": True}),
    }
    server, thread = _start_server(routes)
    base_url = f"http://127.0.0.1:{server.server_port}"

    monkeypatch.setattr("aethis_cli.commands.iam_cmd._clerk_auth", lambda timeout: "tok")

    try:
        res = runner.invoke(app, ["iam", "users-list", "--org-id", "org_1", "--base-url", base_url])
        assert res.exit_code == 0
        assert "u_1" in res.output

        res = runner.invoke(
            app,
            ["iam", "groups-create", "eng", "--name", "Engineering", "--org-id", "org_1", "--base-url", base_url],
        )
        assert res.exit_code == 0
        assert "created" in res.output.lower()
    finally:
        server.shutdown()
        thread.join(timeout=1)


def test_iam_structured_deny_is_rendered(monkeypatch) -> None:
    routes = {
        (
            "POST",
            "/api/v1/iam/users/u_1/roles",
        ): (
            403,
            {
                "detail": {
                    "reason_code": "denied_missing_permission",
                    "action": "iam.admin",
                    "missing_permissions": ["iam:admin"],
                    "message": "IAM admin permission required",
                }
            },
        )
    }
    server, thread = _start_server(routes)
    base_url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr("aethis_cli.commands.iam_cmd._clerk_auth", lambda timeout: "tok")

    try:
        res = runner.invoke(
            app,
            [
                "iam",
                "grant-role",
                "u_1",
                "--org-id",
                "org_1",
                "--domain",
                "core",
                "--role",
                "author",
                "--base-url",
                base_url,
            ],
        )
        assert res.exit_code == 1
        assert "reason=denied_missing_permission" in res.output
        assert "missing=iam:admin" in res.output
    finally:
        server.shutdown()
        thread.join(timeout=1)


def test_iam_uses_env_access_token(monkeypatch) -> None:
    monkeypatch.setenv("AETHIS_ACCESS_TOKEN", "tok_env")
    from aethis_cli.commands.iam_cmd import _clerk_auth

    assert _clerk_auth(120) == "tok_env"
