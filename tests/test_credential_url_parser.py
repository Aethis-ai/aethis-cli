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
        "https://host:",
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
        " https://h",
        "https://h\t.x",
        "https://h /x",
        "http://10.0.0.5",
        "http://192.168.1.2",
        "http://0.0.0.0",
        "http://host.docker.internal",
        "https://h/p?",
        "https://h#",
        "https://[::1]evil",
        "https://[::1]]",
        "https://h:0443",
        "https://[::1",
        "https://[h]",
        "https://\u2100.com",
        "https://\uff45xample.com",
        "https://h%41",
        "https://h%00x",
        "https://h\x00.x",
        "https://h\x7f.x",
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


def test_save_guard_compares_canonical_forms_with_unnormalised_profile() -> None:
    config.set_profile("p", base_url="HTTPS://EXAMPLE.test:443/")
    config.check_save_target("https://example.test", "env", "p")
    with pytest.raises(ConfigError):
        config.check_save_target("https://other.test", "env", "p")


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("https://u:FAKEPW@h", "FAKEPW"),
        ("https://h/?token=FAKETOK", "FAKETOK"),
        ("https://h/#FAKEFRAG", "FAKEFRAG"),
        ("https://h:FAKEPORT", "FAKEPORT"),
    ],
)
def test_error_text_never_contains_the_raw_url(raw: str, secret: str) -> None:
    with pytest.raises(ConfigError) as exc:
        parse_credential_base_url(raw)
    assert secret not in str(exc.value)


@pytest.mark.parametrize(
    ("raw", "secret"),
    [("https://u:FAKEPW@h", "FAKEPW"), ("https://h/?token=FAKETOK", "FAKETOK"), ("https://h/#FAKEFRAG", "FAKEFRAG")],
)
@patch("aethis_cli.commands.login_cmd.run_browser_login")
@patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
def test_cli_output_never_contains_the_raw_url(auth, browser, raw: str, secret: str, monkeypatch) -> None:
    monkeypatch.setenv("AETHIS_BASE_URL", raw)
    for argv in (["account", "keys"], ["login"]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 1
        assert secret not in result.output
    auth.assert_not_called()
    browser.assert_not_called()


@patch("aethis_cli.commands.login_cmd.run_browser_login")
@patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
def test_markup_in_url_does_not_crash_error_output(auth, browser, monkeypatch) -> None:
    monkeypatch.setenv("AETHIS_BASE_URL", "https://example.invalid/[/x]?")
    for argv in (["account", "keys"], ["login"]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)


@patch("aethis_cli.commands.login_cmd._save_key")
@patch("aethis_cli.commands.login_cmd.run_browser_login", return_value="ak_fake")
def test_markup_in_valid_path_does_not_crash_target_line(browser, save, monkeypatch) -> None:
    monkeypatch.setenv("AETHIS_BASE_URL", "https://example.invalid/[bold]x")
    config.set_profile("default", base_url="https://example.invalid/[bold]x")
    result = runner.invoke(app, ["login"])
    assert result.exit_code == 0
    assert "[bold]x" in result.output


@pytest.mark.parametrize(
    "argv",
    [
        ["--profile", "anonymous", "login"],
        ["--profile", "anonymous", "login", "--api-key", "ak_fake"],
        ["--profile", "anonymous", "account", "generate"],
    ],
)
@pytest.mark.parametrize("via_env", [False, True])
@patch("aethis_cli.commands.login_cmd._validate_key")
@patch("aethis_cli.commands.login_cmd.run_browser_login")
@patch("aethis_cli.commands.account_cmd._fetch_permissions", return_value=([], {"decide"}))
@patch("aethis_cli.commands.account_cmd.httpx.post")
@patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
def test_reserved_anonymous_profile_refused_before_any_call(
    auth, post, perms, browser, validate, argv, via_env, monkeypatch
) -> None:
    if via_env:
        monkeypatch.setenv("AETHIS_PROFILE", "anonymous")
        argv = argv[2:]
    result = runner.invoke(app, argv)
    assert result.exit_code == 1
    auth.assert_not_called()
    post.assert_not_called()
    perms.assert_not_called()
    browser.assert_not_called()
    validate.assert_not_called()


@patch("aethis_cli.commands.account_cmd.httpx.delete")
@patch("aethis_cli.commands.account_cmd._clerk_auth", return_value="tok")
def test_markup_in_valid_path_does_not_crash_account_target_line(auth, dele, monkeypatch) -> None:
    monkeypatch.setenv("AETHIS_BASE_URL", "https://example.invalid/[bold]x")
    dele.return_value = MagicMock(status_code=204)
    result = runner.invoke(app, ["account", "revoke", "ak_x", "--yes"])
    assert result.exit_code == 0
    assert "[bold]x" in result.output
