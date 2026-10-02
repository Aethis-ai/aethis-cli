"""Tests for aethis account commands — generate, keys, revoke."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from aethis_cli import config
from aethis_cli.auth_helpers import RUNTIME
from aethis_cli.commands.account_cmd import VALID_SCOPES
from aethis_cli.config import DEFAULT_BASE_URL
from aethis_cli.main import app

runner = CliRunner()

MOCK_ACCESS_TOKEN = "eyJ.mock.jwt"
STAGING_URL = "https://staging.example.test"


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test off the real credentials file, env and project config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    monkeypatch.delenv("AETHIS_PROFILE", raising=False)
    monkeypatch.chdir(tmp_path)
    RUNTIME.base_url_override = None
    RUNTIME.profile_override = None
    return tmp_path


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
    @patch("aethis_cli.commands.account_cmd.save_api_key")
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_generate_success(self, mock_auth, mock_post, mock_save, mock_permissions):
        mock_post.return_value = MagicMock(status_code=201, json=MagicMock(return_value=MOCK_KEY_RESPONSE))

        result = runner.invoke(app, ["account", "generate", "--name", "test-key"])
        assert result.exit_code == 0
        assert "ak_test123" in result.output
        assert "ak_live_abcdef123456" in result.output
        mock_save.assert_called_once_with("ak_live_abcdef123456")

    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd.save_api_key")
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_generate_no_save(self, mock_auth, mock_post, mock_save, mock_permissions):
        mock_post.return_value = MagicMock(status_code=201, json=MagicMock(return_value=MOCK_KEY_RESPONSE))

        result = runner.invoke(app, ["account", "generate", "--no-save"])
        assert result.exit_code == 0
        assert "ak_live_abcdef123456" in result.output
        assert "--no-save" in result.output
        mock_save.assert_not_called()

    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_generate_api_failure(self, mock_auth, mock_post, mock_permissions):
        mock_post.return_value = MagicMock(status_code=500, text="Internal error")

        result = runner.invoke(app, ["account", "generate"])
        assert result.exit_code == 1
        assert "500" in result.output

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
    @patch("aethis_cli.commands.account_cmd.save_api_key")
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_generate_sends_bearer_token(self, mock_auth, mock_post, mock_save, mock_permissions):
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

        # Patch _fetch_permissions AFTER reload — the reload resets module
        # globals, so any patch applied via decorator is lost. Without the
        # patch, the CLI hits the live API for permissions and (until
        # aethis-core 0.10.0 deploys) gets back the legacy bundles:* names,
        # which makes scope validation fail before reaching the Clerk check.
        with patch.object(mod, "_fetch_permissions", return_value=([], set(mod.VALID_SCOPES))):
            result = runner.invoke(reloaded_app, ["account", "generate"])

        assert result.exit_code == 1
        assert "AETHIS_CLERK_CLIENT_ID" in result.output


def _make_staging_profile() -> None:
    config.set_profile("staging", api_key="ak_fake_staging", base_url=STAGING_URL)


class TestAccountUsesSelectedProfileServer:
    @patch("aethis_cli.commands.account_cmd.httpx.delete")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_revoke_targets_profile_server(self, mock_auth, mock_delete):
        _make_staging_profile()
        mock_delete.return_value = MagicMock(status_code=204)

        result = runner.invoke(app, ["--profile", "staging", "account", "revoke", "ak_x", "--yes"])

        assert result.exit_code == 0
        assert mock_delete.call_args.args[0] == f"{STAGING_URL}/api/v1/keys/ak_x"

    @patch("aethis_cli.commands.account_cmd.httpx.get")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_keys_targets_profile_server(self, mock_auth, mock_get):
        _make_staging_profile()
        mock_get.return_value = MagicMock(status_code=200, json=MagicMock(return_value=[]))

        result = runner.invoke(app, ["--profile", "staging", "account", "keys"])

        assert result.exit_code == 0
        assert mock_get.call_args.args[0] == f"{STAGING_URL}/api/v1/keys/"

    @patch("aethis_cli.commands.account_cmd.httpx.get")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_keys_targets_profile_from_env_and_sticky_default(self, mock_auth, mock_get, monkeypatch):
        _make_staging_profile()
        mock_get.return_value = MagicMock(status_code=200, json=MagicMock(return_value=[]))

        monkeypatch.setenv("AETHIS_PROFILE", "staging")
        runner.invoke(app, ["account", "keys"])
        assert mock_get.call_args.args[0] == f"{STAGING_URL}/api/v1/keys/"

        monkeypatch.delenv("AETHIS_PROFILE")
        config.set_active_profile("staging")
        runner.invoke(app, ["account", "keys"])
        assert mock_get.call_args.args[0] == f"{STAGING_URL}/api/v1/keys/"

    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_generate_mints_on_profile_server_and_saves_to_that_profile(self, mock_auth, mock_post, mock_perms):
        _make_staging_profile()
        mock_post.return_value = MagicMock(status_code=201, json=MagicMock(return_value=MOCK_KEY_RESPONSE))

        result = runner.invoke(app, ["--profile", "staging", "account", "generate"])

        assert result.exit_code == 0
        mock_perms.assert_called_once_with(STAGING_URL)
        assert mock_post.call_args.args[0] == f"{STAGING_URL}/api/v1/keys/"
        staging = config.get_profile("staging")
        assert staging["api_key"] == "ak_live_abcdef123456"
        assert staging["base_url"] == STAGING_URL
        assert config.get_profile("default").get("api_key") != "ak_live_abcdef123456"

    @patch("aethis_cli.commands.account_cmd.httpx.delete")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_env_base_url_beats_profile(self, mock_auth, mock_delete, monkeypatch):
        _make_staging_profile()
        monkeypatch.setenv("AETHIS_BASE_URL", "https://env.example.test")
        mock_delete.return_value = MagicMock(status_code=204)

        runner.invoke(app, ["--profile", "staging", "account", "revoke", "ak_x", "--yes"])

        assert mock_delete.call_args.args[0] == "https://env.example.test/api/v1/keys/ak_x"

    @patch("aethis_cli.commands.account_cmd.httpx.delete")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_default_profile_still_uses_default_server(self, mock_auth, mock_delete):
        mock_delete.return_value = MagicMock(status_code=204)

        runner.invoke(app, ["account", "revoke", "ak_x", "--yes"])

        assert mock_delete.call_args.args[0] == f"{DEFAULT_BASE_URL}/api/v1/keys/ak_x"


class TestAccountPrintsTargetBeforeMutating:
    @patch("aethis_cli.commands.account_cmd.httpx.delete")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_revoke_prints_server_before_request(self, mock_auth, mock_delete):
        _make_staging_profile()
        printed_at_request: list[str] = []
        outputs: list[str] = []

        def _delete(*args, **kwargs):
            printed_at_request.append("".join(outputs))
            return MagicMock(status_code=204)

        mock_delete.side_effect = _delete
        with (
            patch(
                "aethis_cli.commands.account_cmd.console.print",
                side_effect=lambda *a, **k: outputs.append(str(a[0]) if a else ""),
            ),
            patch("aethis_cli.commands.account_cmd.info", side_effect=lambda m: outputs.append(m)),
            patch("aethis_cli.commands.account_cmd.success", side_effect=lambda m: outputs.append(m)),
        ):
            result = runner.invoke(app, ["--profile", "staging", "account", "revoke", "ak_x", "--yes"])

        assert result.exit_code == 0
        assert STAGING_URL in printed_at_request[0]

    @patch("aethis_cli.commands.account_cmd.save_api_key")
    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value=MOCK_ACCESS_TOKEN)
    def test_generate_prints_server_before_request(self, mock_auth, mock_post, mock_perms, mock_save):
        _make_staging_profile()
        outputs: list[str] = []
        seen: list[str] = []

        def _post(*args, **kwargs):
            seen.append("".join(outputs))
            return MagicMock(status_code=201, json=MagicMock(return_value=MOCK_KEY_RESPONSE))

        mock_post.side_effect = _post
        with (
            patch(
                "aethis_cli.commands.account_cmd.console.print",
                side_effect=lambda *a, **k: outputs.append(str(a[0]) if a else ""),
            ),
            patch("aethis_cli.commands.account_cmd.info", side_effect=lambda m: outputs.append(m)),
            patch("aethis_cli.commands.account_cmd.success", side_effect=lambda m: outputs.append(m)),
        ):
            result = runner.invoke(app, ["--profile", "staging", "account", "generate"])

        assert result.exit_code == 0
        assert STAGING_URL in seen[0]
