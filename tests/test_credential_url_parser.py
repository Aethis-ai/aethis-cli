"""parse_credential_base_url returns the canonical URL or raises; it never rewrites silently."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from aethis_cli import config
from aethis_cli.auth_helpers import RUNTIME
from aethis_cli.config import ConfigError, parse_credential_base_url
from aethis_cli.main import app

runner = CliRunner()


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("https://api.aethis.ai", "https://api.aethis.ai"),
        ("HTTPS://API.aethis.ai:443/", "https://api.aethis.ai"),
        ("https://Host/Prefix/", "https://host/Prefix"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("http://127.0.0.1", "http://127.0.0.1"),
        ("http://127.1.2.3:9", "http://127.1.2.3:9"),
        ("http://[::1]:8080", "http://[::1]:8080"),
        ("https://host:8443", "https://host:8443"),
        ("http://LOCALHOST:80/", "http://localhost"),
    ],
)
def test_accepted(raw: str, canonical: str) -> None:
    assert parse_credential_base_url(raw) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://h",
        "api.aethis.ai",
        "//evil",
        "",
        "https://",
        "https://host:abc",
        "https://host:99999",
        "https://host:0",
        "https://user:pass@host",
        "https://user@host",
        "https://h/?q=1",
        "https://h/#f",
        "http://remote.example.test",
        "http://foo.localhost",
        "http://localhost.attacker.example",
        "http://128.0.0.1",
    ],
)
def test_refused(raw: str) -> None:
    with pytest.raises(ConfigError):
        parse_credential_base_url(raw)


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for var in ("AETHIS_API_KEY", "AETHIS_BASE_URL", "AETHIS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    RUNTIME.base_url_override = None
    RUNTIME.profile_override = None
    return tmp_path


@patch("aethis_cli.commands.account_cmd.httpx.delete")
@patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
def test_path_prefixed_profile_keeps_prefix(auth, dele) -> None:
    config.set_profile("p", base_url="https://host.example.test/Prefix/")
    dele.return_value = MagicMock(status_code=204)
    result = runner.invoke(app, ["--profile", "p", "account", "revoke", "ak_x", "--yes"])
    assert result.exit_code == 0
    assert dele.call_args.args[0] == "https://host.example.test/Prefix/api/v1/keys/ak_x"


@patch("aethis_cli.commands.status_cmd.AethisClient")
@patch("aethis_cli.commands.status_cmd.resolve_cached_key", return_value="ak_fake")
def test_status_does_not_send_key_to_remote_http_profile(key, client, monkeypatch) -> None:
    from aethis_cli.commands.status_cmd import _print_generation_section, _print_identity_section

    config.set_profile("plain", base_url="http://remote.example.test")
    monkeypatch.setenv("AETHIS_PROFILE", "plain")
    for fn in (lambda: _print_identity_section(), lambda: _print_generation_section("proj")):
        try:
            fn()
        except Exception:
            pass
    client.assert_not_called()


@pytest.mark.parametrize("bad", ["https://host:abc", "api.aethis.ai", "https://u:p@host"])
@patch("aethis_cli.commands.account_cmd.httpx.get")
@patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
@patch("aethis_cli.commands.login_cmd.run_browser_login")
def test_malformed_stored_profile_url_refuses_before_network(browser, auth, get, bad) -> None:
    config.set_profile("bad", base_url=bad)
    r1 = runner.invoke(app, ["--profile", "bad", "account", "keys"])
    r2 = runner.invoke(app, ["login", "--profile", "bad"])
    assert r1.exit_code == 1 and r2.exit_code == 1
    auth.assert_not_called()
    get.assert_not_called()
    browser.assert_not_called()


@patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], {"decide"}))
@patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
def test_malformed_stored_profile_url_refuses_save_guard(auth, perms, monkeypatch) -> None:
    config.set_profile("bad", base_url="https://host:abc")
    monkeypatch.setenv("AETHIS_BASE_URL", "https://example.test")
    result = runner.invoke(app, ["--profile", "bad", "account", "generate"])
    assert result.exit_code == 1
    auth.assert_not_called()
