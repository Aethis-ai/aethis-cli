"""Browser-based OAuth/PKCE authentication with Clerk for CLI key creation."""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from aethis_cli.errors import AuthenticationError

_PORT_RANGE = range(9876, 9886)
_SUCCESS_HTML = """\
<!DOCTYPE html>
<html><head><title>Aethis CLI</title></head>
<body style="font-family:system-ui;display:flex;justify-content:center;align-items:center;height:90vh">
<div style="text-align:center">
<h2>Authenticated</h2>
<p>You can close this tab and return to the terminal.</p>
</div></body></html>"""


def generate_pkce_pair() -> tuple[str, str]:
    """Generate (code_verifier, code_challenge) per RFC 7636."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    """Single-use handler that captures the OAuth callback."""

    def do_GET(self) -> None:  # noqa: N802
        qs = parse_qs(urlparse(self.path).query)
        self.server._auth_code = qs.get("code", [None])[0]  # type: ignore[attr-defined]
        self.server._auth_state = qs.get("state", [None])[0]  # type: ignore[attr-defined]
        self.server._auth_error = qs.get("error", [None])[0]  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(_SUCCESS_HTML.encode())

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # Suppress request logging


class OAuthCallbackServer:
    """Ephemeral HTTP server on 127.0.0.1 to catch Clerk's redirect."""

    def __init__(self) -> None:
        self._server: Optional[HTTPServer] = None
        self.port: int = 0

    def start(self) -> int:
        """Bind to an available port and start serving in a background thread.

        Returns the port number.
        """
        for port in _PORT_RANGE:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
                sock.close()
                self._server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
                self._server._auth_code = None  # type: ignore[attr-defined]
                self._server._auth_state = None  # type: ignore[attr-defined]
                self._server._auth_error = None  # type: ignore[attr-defined]
                self.port = port
                thread = threading.Thread(target=self._server.handle_request, daemon=True)
                thread.start()
                return port
            except OSError:
                continue
        raise AuthenticationError("Could not bind to any port in range 9876-9885")

    def result(self, timeout: float) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Wait for the callback and return (code, state, error)."""
        if not self._server:
            raise AuthenticationError("Server not started")
        self._server.timeout = timeout
        # The handle_request in the thread will complete; wait for it.
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = self._server._auth_code  # type: ignore[attr-defined]
            state = self._server._auth_state  # type: ignore[attr-defined]
            error = self._server._auth_error  # type: ignore[attr-defined]
            if code or error:
                return code, state, error
            time.sleep(0.2)
        return None, None, None

    def shutdown(self) -> None:
        if self._server:
            self._server.server_close()


def authenticate_with_clerk(
    clerk_domain: str,
    client_id: str,
    timeout: int = 120,
) -> str:
    """Run full OAuth/PKCE flow and return an access token (JWT).

    Opens the user's browser to the Clerk sign-in page. After authentication,
    Clerk redirects to a localhost callback. The authorization code is exchanged
    for an access token.

    Raises AuthenticationError on failure or timeout.
    """
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)

    server = OAuthCallbackServer()
    port = server.start()

    redirect_uri = f"http://127.0.0.1:{port}/callback"
    authorize_url = f"https://{clerk_domain}/oauth/authorize?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid profile email",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })

    try:
        opened = webbrowser.open(authorize_url)
        if not opened:
            raise OSError("webbrowser.open returned False")
    except OSError:
        # Headless / SSH fallback
        from aethis_cli.output import console

        console.print("\n[yellow]Could not open browser automatically.[/yellow]")
        console.print("Open this URL in your browser:\n")
        console.print(f"  [bold]{authorize_url}[/bold]\n")

    code, returned_state, error = server.result(timeout)
    server.shutdown()

    if error:
        raise AuthenticationError(f"Clerk returned error: {error}")
    if not code:
        raise AuthenticationError(
            f"Authentication timed out after {timeout}s. "
            "Run the command again or use 'aethis login' to paste a key directly."
        )
    if returned_state != state:
        raise AuthenticationError("State mismatch — possible CSRF attack. Aborting.")

    # Exchange authorization code for access token
    token_url = f"https://{clerk_domain}/oauth/token"
    resp = httpx.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        timeout=15.0,
    )

    if resp.status_code != 200:
        raise AuthenticationError(f"Token exchange failed (HTTP {resp.status_code}): {resp.text}")

    token_data = resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise AuthenticationError("No access_token in token response")

    return access_token
