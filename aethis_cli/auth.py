"""Browser-based OAuth/PKCE authentication with Clerk for CLI key creation."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from aethis_cli.errors import AuthenticationError

_CALLBACK_PORT = 9876
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


class _AuthHTTPServer(HTTPServer):
    """HTTPServer subclass with typed auth attributes."""

    auth_code: Optional[str] = None
    auth_state: Optional[str] = None
    auth_error: Optional[str] = None


class _CallbackHandler(BaseHTTPRequestHandler):
    """Handler that captures the OAuth callback, ignoring other requests (e.g. favicon)."""

    server: _AuthHTTPServer

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            # Ignore favicon and other requests
            self.send_response(404)
            self.end_headers()
            return
        qs = parse_qs(parsed.query)
        self.server.auth_code = qs.get("code", [None])[0]
        self.server.auth_state = qs.get("state", [None])[0]
        self.server.auth_error = qs.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(_SUCCESS_HTML.encode())

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # Suppress request logging


class OAuthCallbackServer:
    """Ephemeral HTTP server on 127.0.0.1 to catch Clerk's redirect."""

    def __init__(self) -> None:
        self._server: Optional[_AuthHTTPServer] = None
        self.port: int = 0

    def _serve_until_auth(self) -> None:
        """Handle requests until we get the auth callback or server is closed."""
        assert self._server is not None
        while not (self._server.auth_code or self._server.auth_error):
            self._server.handle_request()

    def start(self) -> int:
        """Bind to port 9876 and start serving in a background thread.

        Returns the port number. Uses a fixed port to match the redirect URI
        registered with the OAuth provider.
        """
        port = _CALLBACK_PORT
        try:
            self._server = _AuthHTTPServer(("127.0.0.1", port), _CallbackHandler)
            self._server.timeout = 5.0  # Per-request timeout for the loop
            self.port = port
            thread = threading.Thread(target=self._serve_until_auth, daemon=True)
            thread.start()
            return port
        except OSError:
            raise AuthenticationError(
                f"Port {port} is already in use. Close the process using it and try again."
            )

    def result(self, timeout: float) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Wait for the callback and return (code, state, error)."""
        if not self._server:
            raise AuthenticationError("Server not started")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = self._server.auth_code
            state = self._server.auth_state
            error = self._server.auth_error
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
    """Run full OAuth/PKCE flow and return an access token.

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
        "scope": "profile email",
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
        raise AuthenticationError(
            f"Token exchange failed (HTTP {resp.status_code}). "
            "Check your Clerk OAuth configuration and try again."
        )

    token_data = resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise AuthenticationError("No access_token in token response")

    return access_token
