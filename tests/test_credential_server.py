"""Credential-bearing commands (account, login) pick the server from
AETHIS_BASE_URL > active profile > default — never from a project aethis.yaml —
and refuse to save a key minted on a server the target profile does not name."""

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

STAGING_URL = "https://staging.example.test"
ENV_URL = "https://env.example.test"
ATTACKER = "https://attacker.example"
KEY_RESPONSE = {"key_id": "ak_t", "full_key": "ak_live_fake", "name": "n", "scopes": ["decide"]}


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    monkeypatch.delenv("AETHIS_PROFILE", raising=False)
    # A hostile project file in a PARENT of the working directory.
    (tmp_path / "aethis.yaml").write_text(f"project: x\nbase_url: {ATTACKER}\n")
    work = tmp_path / "sub" / "deeper"
    work.mkdir(parents=True)
    monkeypatch.chdir(work)
    RUNTIME.base_url_override = None
    RUNTIME.profile_override = None
    return tmp_path


def _staging() -> None:
    config.set_profile("staging", api_key="ak_fake_staging", base_url=STAGING_URL)


def _urls(*mocks: MagicMock) -> list[str]:
    return [c.args[0] for m in mocks for c in m.call_args_list]


@patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
@patch("aethis_cli.commands.account_cmd.save_api_key")
@patch("aethis_cli.commands.account_cmd.httpx.post")
@patch("aethis_cli.commands.account_cmd.httpx.get")
@patch("aethis_cli.commands.account_cmd.httpx.delete")
@patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
class TestProjectFileCannotChooseServer:
    def test_default_profile_ignores_project_yaml(self, auth, dele, get, post, save, perms):
        dele.return_value = MagicMock(status_code=204)
        get.return_value = MagicMock(status_code=200, json=MagicMock(return_value=[]))
        post.return_value = MagicMock(status_code=201, json=MagicMock(return_value=KEY_RESPONSE))
        runner.invoke(app, ["account", "keys"])
        runner.invoke(app, ["account", "revoke", "ak_x", "--yes"])
        runner.invoke(app, ["account", "generate"])
        urls = _urls(dele, get, post) + [c.args[0] for c in perms.call_args_list]
        assert urls and all(u.startswith(DEFAULT_BASE_URL) for u in urls)
        assert not any(ATTACKER in u for u in urls)

    def test_named_profile_ignores_project_yaml(self, auth, dele, get, post, save, perms):
        _staging()
        dele.return_value = MagicMock(status_code=204)
        get.return_value = MagicMock(status_code=200, json=MagicMock(return_value=[]))
        post.return_value = MagicMock(status_code=201, json=MagicMock(return_value=KEY_RESPONSE))
        runner.invoke(app, ["--profile", "staging", "account", "keys"])
        runner.invoke(app, ["--profile", "staging", "account", "revoke", "ak_x", "--yes"])
        runner.invoke(app, ["--profile", "staging", "account", "generate"])
        urls = _urls(dele, get, post)
        assert urls and all(u.startswith(STAGING_URL) for u in urls)


class TestGenerateSaveGuard:
    @patch("aethis_cli.commands.account_cmd.save_api_key")
    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
    def test_env_server_differing_from_profile_refuses_before_network(self, auth, post, perms, save, monkeypatch):
        _staging()
        monkeypatch.setenv("AETHIS_BASE_URL", ENV_URL)
        result = runner.invoke(app, ["--profile", "staging", "account", "generate"])
        assert result.exit_code != 0
        assert "--no-save" in result.output
        auth.assert_not_called()
        post.assert_not_called()
        perms.assert_not_called()
        save.assert_not_called()

    @patch("aethis_cli.commands.account_cmd.save_api_key")
    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
    def test_no_save_allowed_with_env_server(self, auth, post, perms, save, monkeypatch):
        _staging()
        monkeypatch.setenv("AETHIS_BASE_URL", ENV_URL)
        post.return_value = MagicMock(status_code=201, json=MagicMock(return_value=KEY_RESPONSE))
        result = runner.invoke(app, ["--profile", "staging", "account", "generate", "--no-save"])
        assert result.exit_code == 0
        assert post.call_args.args[0] == f"{ENV_URL}/api/v1/keys/"
        save.assert_not_called()

    @patch("aethis_cli.commands.account_cmd.save_api_key")
    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
    def test_env_matching_profile_is_allowed(self, auth, post, perms, save, monkeypatch):
        _staging()
        monkeypatch.setenv("AETHIS_BASE_URL", STAGING_URL)
        post.return_value = MagicMock(status_code=201, json=MagicMock(return_value=KEY_RESPONSE))
        result = runner.invoke(app, ["--profile", "staging", "account", "generate"])
        assert result.exit_code == 0
        save.assert_called_once()

    @patch("aethis_cli.commands.account_cmd.httpx.delete")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
    def test_target_line_names_source(self, auth, dele, monkeypatch):
        _staging()
        dele.return_value = MagicMock(status_code=204)
        out = runner.invoke(app, ["--profile", "staging", "account", "revoke", "ak_x", "--yes"]).output
        assert "profile" in out and STAGING_URL in out
        monkeypatch.setenv("AETHIS_BASE_URL", ENV_URL)
        out = runner.invoke(app, ["--profile", "staging", "account", "revoke", "ak_x", "--yes"]).output
        assert "AETHIS_BASE_URL" in out and ENV_URL in out


@patch("aethis_cli.commands.login_cmd._save_key")
@patch("aethis_cli.commands.login_cmd.run_browser_login", return_value="ak_live_fake")
class TestLoginServer:
    def test_login_uses_profile_server_not_project_yaml(self, browser, save):
        _staging()
        result = runner.invoke(app, ["--profile", "staging", "login"])
        assert result.exit_code == 0
        assert browser.call_args.args[0] == STAGING_URL

    def test_login_default_ignores_project_yaml(self, browser, save):
        runner.invoke(app, ["login"])
        assert browser.call_args.args[0] == DEFAULT_BASE_URL

    def test_login_refuses_env_server_differing_from_profile(self, browser, save, monkeypatch):
        _staging()
        monkeypatch.setenv("AETHIS_BASE_URL", ENV_URL)
        result = runner.invoke(app, ["--profile", "staging", "login"])
        assert result.exit_code != 0
        browser.assert_not_called()

    @patch("aethis_cli.commands.login_cmd._validate_key", return_value=True)
    def test_login_api_key_path_uses_profile_server(self, validate, browser, save):
        _staging()
        result = runner.invoke(app, ["--profile", "staging", "login", "--api-key", "ak_fake"])
        assert result.exit_code == 0
        assert validate.call_args.args[1] == STAGING_URL


class TestUrlValidationAndNormalisation:
    @patch("aethis_cli.commands.account_cmd.httpx.get")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
    def test_plaintext_remote_profile_refused_for_account(self, auth, get):
        config.set_profile("plain", api_key="ak_fake", base_url="http://remote.example.test")
        result = runner.invoke(app, ["--profile", "plain", "account", "keys"])
        assert result.exit_code == 1
        assert "HTTP" in result.output
        auth.assert_not_called()
        get.assert_not_called()

    @patch("aethis_cli.commands.account_cmd.httpx.get")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
    def test_plaintext_remote_env_refused(self, auth, get, monkeypatch):
        monkeypatch.setenv("AETHIS_BASE_URL", "http://remote.example.test")
        result = runner.invoke(app, ["account", "keys"])
        assert result.exit_code == 1
        get.assert_not_called()

    @patch("aethis_cli.commands.login_cmd.run_browser_login")
    def test_plaintext_remote_profile_refused_for_login(self, browser):
        config.set_profile("plain", base_url="http://remote.example.test")
        result = runner.invoke(app, ["login", "--profile", "plain"])
        assert result.exit_code == 1
        browser.assert_not_called()

    @patch("aethis_cli.commands.account_cmd.httpx.delete")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
    def test_localhost_http_allowed(self, auth, dele):
        config.set_profile("local", base_url="http://localhost:8080/")
        dele.return_value = MagicMock(status_code=204)
        result = runner.invoke(app, ["--profile", "local", "account", "revoke", "ak_x", "--yes"])
        assert result.exit_code == 0
        assert dele.call_args.args[0] == "http://localhost:8080/api/v1/keys/ak_x"

    @patch("aethis_cli.commands.account_cmd.httpx.delete")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
    def test_trailing_slash_env_does_not_double_slash(self, auth, dele, monkeypatch):
        monkeypatch.setenv("AETHIS_BASE_URL", f"{DEFAULT_BASE_URL}/")
        dele.return_value = MagicMock(status_code=204)
        runner.invoke(app, ["account", "revoke", "ak_x", "--yes"])
        assert dele.call_args.args[0] == f"{DEFAULT_BASE_URL}/api/v1/keys/ak_x"

    @pytest.mark.parametrize(
        "env_url",
        ["https://EXAMPLE.test", "https://example.test:443", "https://example.test/", "HTTPS://Example.Test:443/"],
    )
    @patch("aethis_cli.commands.account_cmd.save_api_key")
    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd.httpx.post")
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
    def test_equivalent_origins_are_not_refused(self, auth, post, perms, save, env_url, monkeypatch):
        config.set_profile("p", api_key="ak_fake", base_url="https://example.test")
        monkeypatch.setenv("AETHIS_BASE_URL", env_url)
        post.return_value = MagicMock(status_code=201, json=MagicMock(return_value=KEY_RESPONSE))
        result = runner.invoke(app, ["--profile", "p", "account", "generate"])
        assert result.exit_code == 0
        assert post.call_args.args[0] == "https://example.test/api/v1/keys/"

    @patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], set(VALID_SCOPES)))
    @patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
    def test_different_port_is_refused(self, auth, perms, monkeypatch):
        config.set_profile("p", api_key="ak_fake", base_url="https://example.test")
        monkeypatch.setenv("AETHIS_BASE_URL", "https://example.test:8443")
        result = runner.invoke(app, ["--profile", "p", "account", "generate"])
        assert result.exit_code == 1


@patch("aethis_cli.commands.login_cmd._save_key")
@patch("aethis_cli.commands.login_cmd.run_browser_login", return_value="ak_live_fake")
class TestLoginProfileOption:
    def test_login_profile_option_targets_that_profile_server(self, browser, save):
        _staging()
        result = runner.invoke(app, ["login", "--profile", "staging"])
        assert result.exit_code == 0
        assert browser.call_args.args[0] == STAGING_URL
        assert browser.call_args.kwargs["profile"] == "staging"

    @patch("aethis_cli.commands.login_cmd._validate_key", return_value=True)
    def test_login_profile_option_api_key_path(self, validate, browser, save):
        _staging()
        result = runner.invoke(app, ["login", "--profile", "staging", "--api-key", "ak_fake"])
        assert result.exit_code == 0
        assert validate.call_args.args[1] == STAGING_URL
        assert save.call_args.kwargs["profile"] == "staging"

    def test_login_target_line_names_source(self, browser, save, monkeypatch):
        _staging()
        out = " ".join(runner.invoke(app, ["login", "--profile", "staging"]).output.split())
        assert f"Target server: {STAGING_URL} (from profile; profile: staging)" in out
        out = " ".join(runner.invoke(app, ["login"]).output.split())
        assert f"Target server: {DEFAULT_BASE_URL} (default; profile: default)" in out
        monkeypatch.setenv("AETHIS_BASE_URL", STAGING_URL)
        out = " ".join(runner.invoke(app, ["login", "--profile", "staging"]).output.split())
        assert f"Target server: {STAGING_URL} (from AETHIS_BASE_URL; profile: staging)" in out

    def test_login_refusal_remedy_is_login_specific(self, browser, save, monkeypatch):
        _staging()
        monkeypatch.setenv("AETHIS_BASE_URL", ENV_URL)
        result = runner.invoke(app, ["login", "--profile", "staging"])
        assert result.exit_code == 1
        browser.assert_not_called()
        save.assert_not_called()
        assert "--no-save" not in result.output
        assert "aethis profile add staging --base-url" in result.output


class TestCheckSaveTargetDirect:
    def test_compares_normalised_origins_for_raw_input(self):
        config.set_profile("p", base_url="https://example.test")
        config.check_save_target("HTTPS://Example.Test:443/", "env", "p")
        with pytest.raises(config.ConfigError):
            config.check_save_target("https://example.test:8443", "env", "p")
