"""Tests for aethis account commands — generate, keys, revoke."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from aethis_cli.main import app
from aethis_cli.commands.account_cmd import VALID_SCOPES

runner = CliRunner()

MOCK_ACCESS_TOKEN = "eyJ.mock.jwt"

MOCK_KEY_RESPONSE = {
    "key_id": "ak_test123",
    "full_key": "ak_live_abcdef123456",
    "name": "test-key",
    "scopes": ["decide"],
    "rate_limit_tier": "free",
    "created_at": "2026-03-29T12:00:00Z",
}

MOCK_KEYS_LIST = [
    {
        "key_id": "ak_test123",
        "name": "test-key",
        "scopes": ["decide"],
        "rate_limit_tier": "free",
        "created_at": "2026-03-29T12:00:00Z",
        "revoked": False,
    },
    {
        "key_id": "ak_test456",
        "name": "prod-key",
        "scopes": ["decide", "projects:write"],
        "rate_limit_tier": "pro",
        "created_at": "2026-03-28T12:00:00Z",
        "revoked": False,
    },
]


class TestAccountGenerate:
    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd._save_to_keyring", return_value=True)
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_generate_success(self, mock_auth, mock_post, mock_keyring, mock_permissions):
        mock_post.return_value = MagicMock(status_code=201, json=MagicMock(return_value=MOCK_KEY_RESPONSE))

        result = runner.invoke(app, ["account", "generate", "--name", "test-key"])
        assert result.exit_code == 0
        assert "ak_test123" in result.output
        assert "ak_live_abcdef123456" in result.output
        assert "saved" in result.output.lower()

    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd._save_to_keyring", return_value=True)
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_generate_no_save(self, mock_auth, mock_post, mock_keyring, mock_permissions):
        mock_post.return_value = MagicMock(status_code=201, json=MagicMock(return_value=MOCK_KEY_RESPONSE))

        result = runner.invoke(app, ["account", "generate", "--no-save"])
        assert result.exit_code == 0
        assert "ak_live_abcdef123456" in result.output
        assert "--no-save" in result.output
        mock_keyring.assert_not_called()

    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_generate_api_failure(self, mock_auth, mock_post, mock_permissions):
        mock_post.return_value = MagicMock(status_code=500, text="Internal error")

        result = runner.invoke(app, ["account", "generate"])
        assert result.exit_code == 1
        assert "500" in result.output

    def test_generate_invalid_scope(self):
        result = runner.invoke(app, ["account", "generate", "--scope", "invalid_scope"])
        # Should fail before auth since scope validation is first... but _clerk_auth is called first
        # Actually scope validation happens after auth. Let's mock auth.
        pass

    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_generate_invalid_scope_rejected(self, mock_auth, mock_permissions):
        result = runner.invoke(app, ["account", "generate", "--scope", "not_a_scope"])
        assert result.exit_code == 1
        assert "Invalid scope" in result.output

    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_generate_invalid_tier_rejected(self, mock_auth, mock_permissions):
        result = runner.invoke(app, ["account", "generate", "--tier", "enterprise"])
        assert result.exit_code == 1
        assert "Invalid tier" in result.output

    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd._save_to_keyring", return_value=True)
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_generate_sends_bearer_token(self, mock_auth, mock_post, mock_keyring, mock_permissions):
        mock_post.return_value = MagicMock(status_code=201, json=MagicMock(return_value=MOCK_KEY_RESPONSE))

        runner.invoke(app, ["account", "generate"])

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "Bearer" in call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {})).get("Authorization", "")


class TestAccountKeys:
    @patch("aethis_cli.commands.account_cmd.httpx.get")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_keys_renders_table(self, mock_auth, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=MagicMock(return_value=MOCK_KEYS_LIST))

        result = runner.invoke(app, ["account", "keys"])
        assert result.exit_code == 0
        assert "ak_test123" in result.output
        assert "ak_test456" in result.output

    @patch("aethis_cli.commands.account_cmd.httpx.get")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_keys_empty(self, mock_auth, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=MagicMock(return_value=[]))

        result = runner.invoke(app, ["account", "keys"])
        assert result.exit_code == 0
        assert "No API keys" in result.output


class TestAccountPermissions:
    @patch("aethis_cli.commands.account_cmd.httpx.get")
    def test_permissions_renders_registry_table(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value=[
                    {
                        "action": "project.write",
                        "required_permissions": ["projects:write"],
                        "description": "Create and mutate projects",
                    }
                ]
            ),
        )
        result = runner.invoke(app, ["account", "permissions"])
        assert result.exit_code == 0
        assert "project.write" in result.output
        assert "projects:write" in result.output

    @patch("aethis_cli.commands.account_cmd.httpx.get")
    def test_permissions_falls_back_when_endpoint_unavailable(self, mock_get):
        mock_get.return_value = MagicMock(status_code=500, json=MagicMock(return_value={"detail": "error"}))
        result = runner.invoke(app, ["account", "permissions"])
        assert result.exit_code == 0
        assert "fallback" in result.output.lower()
        assert "decide" in result.output


class TestApiErrorFormatting:
    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_generate_surfaces_authz_error_detail(self, mock_auth, mock_post, mock_permissions):
        mock_post.return_value = MagicMock(
            status_code=403,
            text="forbidden",
            json=MagicMock(
                return_value={
                    "detail": {
                        "reason_code": "denied_missing_permission",
                        "action": "scope.projects:write",
                        "missing_permissions": ["projects:write"],
                        "message": "API key missing required scope",
                    }
                }
            ),
        )

        result = runner.invoke(app, ["account", "generate"])
        assert result.exit_code == 1
        assert "reason=denied_missing_permission" in result.output
        assert "missing=projects:write" in result.output


class TestAccountRevoke:
    @patch("aethis_cli.commands.account_cmd.httpx.delete")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_revoke_success(self, mock_auth, mock_delete):
        mock_delete.return_value = MagicMock(status_code=204)

        result = runner.invoke(app, ["account", "revoke", "ak_test123", "--yes"])
        assert result.exit_code == 0
        assert "revoked" in result.output.lower()

    @patch("aethis_cli.commands.account_cmd.httpx.delete")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_revoke_not_found(self, mock_auth, mock_delete):
        mock_delete.return_value = MagicMock(status_code=404)

        result = runner.invoke(app, ["account", "revoke", "ak_bad", "--yes"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestClerkConfig:
    @patch.dict("os.environ", {"AETHIS_CLERK_CLIENT_ID": ""}, clear=False)
    def test_missing_client_id_exits(self):
        """Without AETHIS_CLERK_CLIENT_ID, generate should exit with helpful message."""
        # We need to reload the module to pick up the env var
        import importlib
        import aethis_cli.commands.account_cmd as mod

        importlib.reload(mod)
        # Re-import app since the module was reloaded
        from aethis_cli.main import app as reloaded_app

        result = runner.invoke(reloaded_app, ["account", "generate"])
        assert result.exit_code == 1
        assert "AETHIS_CLERK_CLIENT_ID" in result.output
