"""Tests for aethis_cli.auth — OAuth/PKCE helpers and callback server."""

from __future__ import annotations

import base64
import hashlib
from unittest.mock import MagicMock, patch

import httpx
import pytest

from aethis_cli.auth import (
    OAuthCallbackServer,
    authenticate_with_clerk,
    generate_pkce_pair,
)
from aethis_cli.errors import AuthenticationError


class TestPKCE:
    def test_verifier_length(self):
        verifier, _ = generate_pkce_pair()
        assert 43 <= len(verifier) <= 128

    def test_verifier_is_url_safe(self):
        verifier, _ = generate_pkce_pair()
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert all(c in allowed for c in verifier)

    def test_challenge_is_sha256_of_verifier(self):
        verifier, challenge = generate_pkce_pair()
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        )
        assert challenge == expected

    def test_pairs_are_unique(self):
        pairs = [generate_pkce_pair() for _ in range(10)]
        verifiers = [p[0] for p in pairs]
        assert len(set(verifiers)) == 10


class TestCallbackServer:
    def test_server_starts_and_captures_code(self):
        server = OAuthCallbackServer()
        port = server.start()
        assert port == 9876

        # Simulate Clerk redirect
        httpx.get(f"http://127.0.0.1:{port}/callback?code=test_code&state=test_state")

        code, state, error = server.result(timeout=5)
        server.shutdown()

        assert code == "test_code"
        assert state == "test_state"
        assert error is None

    def test_server_captures_error(self):
        server = OAuthCallbackServer()
        port = server.start()

        httpx.get(f"http://127.0.0.1:{port}/callback?error=access_denied")

        _, _, error = server.result(timeout=5)
        server.shutdown()

        assert error == "access_denied"

    def test_server_timeout_returns_none(self):
        server = OAuthCallbackServer()
        server.start()

        code, state, error = server.result(timeout=0.5)
        server.shutdown()

        assert code is None
        assert state is None
        assert error is None


class TestAuthenticateWithClerk:
    @patch("aethis_cli.auth.webbrowser.open", return_value=True)
    @patch("aethis_cli.auth.httpx.post")
    @patch("aethis_cli.auth.OAuthCallbackServer")
    def test_full_flow_success(self, MockServer, mock_post, mock_browser):
        mock_server = MagicMock()
        mock_server.start.return_value = 9876
        # Return matching state — we need to capture what state is generated
        mock_server.result.return_value = ("auth_code_123", None, None)
        MockServer.return_value = mock_server

        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"access_token": "jwt_token_123"}),
        )

        # The state check will fail because mock returns None for state.
        # We need to patch secrets.token_urlsafe to control the state.
        with patch("aethis_cli.auth.secrets.token_urlsafe", return_value="fixed_state"):
            mock_server.result.return_value = ("auth_code_123", "fixed_state", None)
            token = authenticate_with_clerk("clerk.test.com", "client_123", timeout=10)

        assert token == "jwt_token_123"
        mock_browser.assert_called_once()
        mock_post.assert_called_once()

    @patch("aethis_cli.auth.webbrowser.open", return_value=True)
    @patch("aethis_cli.auth.OAuthCallbackServer")
    def test_timeout_raises(self, MockServer, mock_browser):
        mock_server = MagicMock()
        mock_server.start.return_value = 9876
        mock_server.result.return_value = (None, None, None)
        MockServer.return_value = mock_server

        with pytest.raises(AuthenticationError, match="timed out"):
            authenticate_with_clerk("clerk.test.com", "client_123", timeout=1)

    @patch("aethis_cli.auth.webbrowser.open", return_value=True)
    @patch("aethis_cli.auth.OAuthCallbackServer")
    def test_clerk_error_raises(self, MockServer, mock_browser):
        mock_server = MagicMock()
        mock_server.start.return_value = 9876
        mock_server.result.return_value = (None, None, "access_denied")
        MockServer.return_value = mock_server

        with pytest.raises(AuthenticationError, match="access_denied"):
            authenticate_with_clerk("clerk.test.com", "client_123", timeout=10)

    @patch("aethis_cli.auth.webbrowser.open", return_value=True)
    @patch("aethis_cli.auth.httpx.post")
    @patch("aethis_cli.auth.OAuthCallbackServer")
    def test_state_mismatch_raises(self, MockServer, mock_post, mock_browser):
        mock_server = MagicMock()
        mock_server.start.return_value = 9876
        mock_server.result.return_value = ("code", "wrong_state", None)
        MockServer.return_value = mock_server

        with patch("aethis_cli.auth.secrets.token_urlsafe", return_value="correct_state"):
            with pytest.raises(AuthenticationError, match="State mismatch"):
                authenticate_with_clerk("clerk.test.com", "client_123", timeout=10)

    @patch("aethis_cli.auth.webbrowser.open", return_value=True)
    @patch("aethis_cli.auth.httpx.post")
    @patch("aethis_cli.auth.OAuthCallbackServer")
    def test_token_exchange_failure_raises(self, MockServer, mock_post, mock_browser):
        mock_server = MagicMock()
        mock_server.start.return_value = 9876
        MockServer.return_value = mock_server

        with patch("aethis_cli.auth.secrets.token_urlsafe", return_value="s"):
            mock_server.result.return_value = ("code", "s", None)
            mock_post.return_value = MagicMock(status_code=400, text="invalid_grant")

            with pytest.raises(AuthenticationError, match="Token exchange failed"):
                authenticate_with_clerk("clerk.test.com", "client_123", timeout=10)
