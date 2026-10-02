"""A project aethis.yaml must not choose which credential is read or where it is sent.

``aethis.yaml`` files are copied between machines (public example repos), so the
file is untrusted input. A credential may only go to the server the user
selected (``--base-url`` / ``AETHIS_BASE_URL`` > active profile > default), and
only an environment variable the user designated may be read as a key.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx
from typer.testing import CliRunner

from aethis_cli import config
from aethis_cli.auth_helpers import RUNTIME
from aethis_cli.client import AethisClient
from aethis_cli.config import DEFAULT_BASE_URL
from aethis_cli.errors import ConfigError
from aethis_cli.main import app

runner = CliRunner()

ATTACKER = "https://attacker.example"
STAGING = "https://staging.example.test"
ENV_URL = "https://env.example.test"
CACHED = "ak_fake_cached"
STAGING_KEY = "ak_fake_staging"
EVIL_ANTHROPIC = "sk-ant-attacker-chosen"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    # ``--base-url`` / ``--api-key`` write os.environ directly; register both with
    # monkeypatch first so the write is undone and cannot leak into other tests.
    for name in ("AETHIS_BASE_URL", "AETHIS_API_KEY"):
        monkeypatch.setenv(name, "")
    for name in (
        "AETHIS_API_KEY",
        "AETHIS_BASE_URL",
        "AETHIS_PROFILE",
        "AETHIS_API_KEY_ENV",
        "AETHIS_ANTHROPIC_KEY_ENV",
        "AETHIS_DEEPSEEK_KEY_ENV",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    fake_keyring = types.ModuleType("keyring")
    fake_keyring.get_password = lambda *_a, **_k: None  # type: ignore[attr-defined]
    fake_keyring.set_password = lambda *_a, **_k: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    work = tmp_path / "project" / "deeper"
    work.mkdir(parents=True)
    monkeypatch.chdir(work)
    RUNTIME.base_url_override = None
    RUNTIME.profile_override = None
    RUNTIME.api_key_override = None
    RUNTIME.no_prompt = False
    return tmp_path


def _yaml(tmp_path: Path, **kv: str) -> None:
    body = "project: x\n" + "".join(f"{k}: {v}\n" for k, v in kv.items())
    (tmp_path / "project" / "aethis.yaml").write_text(body)


def _cache_default_key() -> None:
    config.set_profile("default", api_key=CACHED)


def _staging() -> None:
    config.set_profile("staging", api_key=STAGING_KEY, base_url=STAGING)


class _Net:
    """Records every HTTP request. Answers 200 {} unless told otherwise via ``on``."""

    def __init__(self, router: Any) -> None:
        self.calls = router.calls
        self._scripted: dict[tuple[str, str], list[httpx.Response]] = {}
        router.route().mock(side_effect=self._answer)

    def on(self, method: str, url: str, *responses: httpx.Response) -> None:
        self._scripted[(method, url)] = list(responses)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        queue = self._scripted.get((request.method, str(request.url.copy_with(query=None))))
        if queue:
            return queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(200, json={})


@pytest.fixture
def net():
    with respx.mock(assert_all_called=False) as router:
        yield _Net(router)


def _requests(net: Any) -> list[httpx.Request]:
    return [c.request for c in net.calls]


def _hosts(net: Any) -> set[str]:
    return {r.url.host for r in _requests(net)}


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    """Strip colour codes and collapse wrapping: Rich colours and re-flows output on CI."""
    return re.sub(r"\s+", " ", _ANSI.sub("", text))


def _msg(result: Any) -> str:
    return _plain((result.output or "") + str(result.exception or ""))


def _no_secret_on_wire(net: Any, *secrets: str) -> None:
    for r in _requests(net):
        blob = json.dumps(dict(r.headers)) + r.content.decode("utf-8", "replace")
        for s in secrets:
            assert s not in blob, f"{s!r} was sent to {r.url.host}"


# --------------------------------------------------------------------------- #
# The one decision: may a credential go to this server?
# --------------------------------------------------------------------------- #


class TestAuthorizeCredentialServer:
    def test_no_request_returns_trusted_server(self):
        assert config.authorize_credential_server() == DEFAULT_BASE_URL

    @pytest.mark.parametrize(
        "requested",
        [
            "https://api.aethis.ai",
            "https://api.aethis.ai/",
            "HTTPS://API.AETHIS.AI",
            "https://api.aethis.ai:443",
        ],
    )
    def test_equal_in_canonical_form_proceeds(self, requested):
        assert config.authorize_credential_server(requested) == DEFAULT_BASE_URL

    def test_different_server_refused_with_remedy_and_no_echo(self):
        with pytest.raises(ConfigError) as ei:
            config.authorize_credential_server(f"{ATTACKER}/secretpath")
        text = str(ei.value)
        assert "AETHIS_BASE_URL" in text and "--base-url" in text and "profile" in text
        assert "attacker" not in text and "secretpath" not in text

    @pytest.mark.parametrize("bad", ["https://u:p@api.aethis.ai", "https://api.aethis.ai?x=1", "ftp://x"])
    def test_unparseable_request_refused_without_echo(self, bad):
        with pytest.raises(ConfigError) as ei:
            config.authorize_credential_server(bad)
        assert "evil.example" not in str(ei.value) and "u:p" not in str(ei.value)

    def test_trusted_server_follows_profile(self):
        _staging()
        RUNTIME.profile_override = "staging"
        assert config.authorize_credential_server(STAGING + "/") == STAGING
        with pytest.raises(ConfigError):
            config.authorize_credential_server(DEFAULT_BASE_URL)

    def test_env_confirms_a_different_server(self, monkeypatch):
        monkeypatch.setenv("AETHIS_BASE_URL", ATTACKER)
        assert config.authorize_credential_server(ATTACKER) == ATTACKER


# --------------------------------------------------------------------------- #
# Hostile parent aethis.yaml base_url: no credential-bearing request, any command
# --------------------------------------------------------------------------- #


class TestHostileBaseUrl:
    @pytest.fixture(autouse=True)
    def _hostile(self, _env):
        _yaml(_env, base_url=ATTACKER)
        _cache_default_key()

    @pytest.mark.parametrize(
        "args",
        [
            ["whoami"],
            ["usage"],
            ["status"],
            ["--output", "json", "status"],
            ["--output", "table", "status", "--project-id", "proj_1"],
            ["--output", "json", "status", "--project-id", "proj_1"],
            ["projects", "list"],
            ["rulesets", "list", "--project-id", "proj_1"],
            ["decide", "-b", "aethis/slug", "-i", "{}"],
            ["review", "proj_1"],
            ["review", "proj_1", "--coach"],
            ["fields", "discover"],
            ["publish"],
            ["test"],
            ["generate"],
        ],
    )
    def test_refused_before_any_request(self, net, args):
        result = runner.invoke(app, args)
        assert result.exit_code != 0
        assert _requests(net) == [], f"{args}: sent {[str(r.url) for r in _requests(net)]}"
        text = _msg(result)
        assert "AETHIS_BASE_URL" in text and "attacker" not in text

    def test_status_still_reports_the_users_own_server(self, net):
        result = runner.invoke(app, ["status"])
        assert DEFAULT_BASE_URL in result.output
        assert "attacker" not in result.output

    def test_status_json_is_valid_and_marks_identity_refused(self, net):
        result = runner.invoke(app, ["--output", "json", "status"])
        assert result.exit_code != 0
        body = json.loads(result.output)
        assert body["server"]["base_url"] == DEFAULT_BASE_URL
        assert "refused" in json.dumps(body["identity"]).lower()

    def test_anonymous_profile_reads_keep_working_without_a_credential(self, net):
        net.on("GET", f"{ATTACKER}/api/v1/public/rulesets", httpx.Response(200, json=[]))
        result = runner.invoke(app, ["--profile", "anonymous", "rulesets", "list", "--public"])
        assert result.exit_code == 0, _msg(result)
        reqs = _requests(net)
        assert reqs and {r.url.host for r in reqs} == {"attacker.example"}  # anonymous reads follow the project
        for r in reqs:
            assert "x-api-key" not in r.headers
            assert "authorization" not in r.headers

    def test_inline_login_refused_before_browser_or_token(self, net):
        config.remove_profile("default")
        with (
            patch("aethis_cli.auth_helpers._is_interactive", return_value=True),
            patch("builtins.input", return_value="y") as prompt,
            patch("aethis_cli.auth.authenticate_with_clerk", return_value="signin-token") as browser,
        ):
            result = runner.invoke(app, ["projects", "list"])
        assert result.exit_code != 0
        prompt.assert_not_called()
        browser.assert_not_called()
        assert _requests(net) == []
        assert "attacker" not in _msg(result)

    def test_invalid_project_url_does_not_leak_or_send(self, net, _env):
        _yaml(_env, base_url="'https://u:topsecret@attacker.example'")
        result = runner.invoke(app, ["whoami"])
        assert _requests(net) == []
        assert "topsecret" not in _msg(result) and "attacker" not in _msg(result)


# --------------------------------------------------------------------------- #
# User-confirmed project servers proceed (and only to the confirmed server)
# --------------------------------------------------------------------------- #


class TestConfirmedServerProceeds:
    def test_yaml_equal_to_profile_server(self, net, _env):
        _staging()
        _yaml(_env, base_url=STAGING + "/")
        result = runner.invoke(app, ["--profile", "staging", "whoami"])
        assert result.exit_code == 0, _msg(result)
        assert _hosts(net) == {"staging.example.test"}
        assert {r.headers["x-api-key"] for r in _requests(net)} == {STAGING_KEY}

    def test_yaml_equal_to_default(self, net, _env):
        _cache_default_key()
        _yaml(_env, base_url=DEFAULT_BASE_URL)
        result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 0, _msg(result)
        assert _hosts(net) == {"api.aethis.ai"}

    def test_env_var_confirms_project_server(self, net, _env, monkeypatch):
        _cache_default_key()
        _yaml(_env, base_url=ATTACKER)
        monkeypatch.setenv("AETHIS_BASE_URL", ATTACKER)
        result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 0, _msg(result)
        assert _hosts(net) == {"attacker.example"}

    def test_flag_confirms_project_server(self, net, _env):
        _cache_default_key()
        _yaml(_env, base_url=ATTACKER)
        result = runner.invoke(app, ["--base-url", ATTACKER, "whoami"])
        assert result.exit_code == 0, _msg(result)
        assert _hosts(net) == {"attacker.example"}

    def test_no_project_server_uses_profile_server_for_project_commands(self, net, _env):
        _staging()
        _yaml(_env)
        result = runner.invoke(app, ["--profile", "staging", "review", "proj_1"])
        assert _hosts(net) == {"staging.example.test"}, _msg(result)


# --------------------------------------------------------------------------- #
# status / whoami / usage use the selected profile's server (the #160 bug)
# --------------------------------------------------------------------------- #


class TestProfileServerForIdentity:
    @pytest.mark.parametrize("fmt", ["table", "json"])
    def test_status_identity_and_generation_hit_profile_server(self, net, fmt):
        _staging()
        result = runner.invoke(app, ["--profile", "staging", "--output", fmt, "status", "--project-id", "proj_1"])
        assert result.exit_code == 0, _msg(result)
        assert _hosts(net) == {"staging.example.test"}
        assert {r.headers["x-api-key"] for r in _requests(net)} == {STAGING_KEY}

    @pytest.mark.parametrize("cmd", ["whoami", "usage"])
    def test_whoami_usage_hit_profile_server(self, net, cmd):
        _staging()
        runner.invoke(app, ["--profile", "staging", cmd])
        assert _hosts(net) == {"staging.example.test"}

    @pytest.mark.parametrize(
        "bad_env",
        ["http://remote.example.test", "https://user:pass@remote.example.test", "https://remote.example.test?x=1"],
    )
    @pytest.mark.parametrize(
        "args", [["--output", "json", "status"], ["--output", "table", "status"], ["whoami"], ["usage"]]
    )
    def test_invalid_server_never_receives_the_key(self, net, monkeypatch, args, bad_env):
        _cache_default_key()
        monkeypatch.setenv("AETHIS_BASE_URL", bad_env)
        runner.invoke(app, args)
        assert _requests(net) == []

    def test_invalid_profile_server_never_receives_the_key(self, net):
        config.set_profile("default", api_key=CACHED, base_url="http://remote.example.test")
        runner.invoke(app, ["--output", "json", "status"])
        assert _requests(net) == []


# --------------------------------------------------------------------------- #
# Sign-in token and minted key: only the credential server, saved to its profile
# --------------------------------------------------------------------------- #


class TestInlineLoginAndRefresh:
    def _interactive(self):
        return (
            patch("aethis_cli.auth_helpers._is_interactive", return_value=True),
            patch("builtins.input", return_value="y"),
        )

    def test_inline_login_sends_token_only_to_profile_server(self, net, _env):
        _yaml(_env)
        config.set_profile("staging", base_url=STAGING)
        net.on(
            "POST", f"{STAGING}/api/v1/keys/", httpx.Response(201, json={"full_key": "ak_live_minted", "key_id": "k"})
        )
        p1, p2 = self._interactive()
        with p1, p2, patch("aethis_cli.auth.authenticate_with_clerk", return_value="signin-token"):
            runner.invoke(app, ["--profile", "staging", "projects", "list"])
        post = [r for r in _requests(net) if r.method == "POST"]
        assert [r.url.host for r in post] == ["staging.example.test"]
        assert post[0].headers["authorization"] == "Bearer signin-token"
        assert config.get_profile("staging")["api_key"] == "ak_live_minted"
        assert all(r.url.host == "staging.example.test" for r in _requests(net))

    def test_401_refresh_sends_token_only_to_profile_server(self, net):
        _staging()
        net.on(
            "GET",
            f"{STAGING}/api/v1/public/projects/",
            httpx.Response(401, json={"detail": "bad"}),
            httpx.Response(200, json=[]),
        )
        net.on("POST", f"{STAGING}/api/v1/keys/", httpx.Response(201, json={"full_key": "ak_live_new", "key_id": "k"}))
        p1, p2 = self._interactive()
        with p1, p2, patch("aethis_cli.auth.authenticate_with_clerk", return_value="signin-token"):
            runner.invoke(app, ["--profile", "staging", "projects", "list"])
        assert _hosts(net) == {"staging.example.test"}
        assert [r.url.host for r in _requests(net) if r.method == "POST"] == ["staging.example.test"]

    def test_env_server_that_is_not_the_profile_server_cannot_receive_a_saved_key(self, net, monkeypatch):
        config.set_profile("staging", base_url=STAGING)
        monkeypatch.setenv("AETHIS_BASE_URL", ENV_URL)
        p1, p2 = self._interactive()
        with p1, p2, patch("aethis_cli.auth.authenticate_with_clerk", return_value="signin-token") as browser:
            result = runner.invoke(app, ["--profile", "staging", "projects", "list"])
        # The user pointed the CLI at ENV_URL but profile 'staging' names another
        # server: a key minted there must not be saved under 'staging'.
        browser.assert_not_called()
        assert [r for r in _requests(net) if r.method == "POST"] == []
        assert "profile" in _msg(result)

    def test_run_browser_login_itself_refuses_a_foreign_server(self, net):
        from aethis_cli.commands.login_cmd import run_browser_login

        with patch("aethis_cli.auth.authenticate_with_clerk", return_value="signin-token") as browser:
            with pytest.raises(ConfigError):
                run_browser_login(ATTACKER)
        browser.assert_not_called()
        assert _requests(net) == []

    def test_make_authed_client_refuses_a_foreign_server(self):
        with pytest.raises(ConfigError):
            config.make_authed_client(CACHED, ATTACKER)


class TestEachEntryPointRefusesOnItsOwn:
    """The guard is called from several sites; each is exercised in isolation so none is merely subsumed."""

    def test_resolve_api_key_refuses_a_foreign_server_even_with_a_cached_key(self):
        _cache_default_key()
        with pytest.raises(ConfigError):
            config.resolve_api_key(config.ProjectConfig(project="x", base_url=ATTACKER))

    def test_init_finds_the_cached_key_of_the_selected_profile_server(self):
        from aethis_cli.commands.init_cmd import _has_cached_auth

        _staging()
        RUNTIME.profile_override = "staging"
        assert _has_cached_auth() is True


# --------------------------------------------------------------------------- #
# Which environment variable may be read as a key
# --------------------------------------------------------------------------- #


class _EnvSpy(dict):
    def __init__(self, base: Any) -> None:
        super().__init__(base)
        self.reads: list[str] = []

    def get(self, key: str, default: Any = None) -> Any:
        self.reads.append(key)
        return super().get(key, default)

    def __getitem__(self, key: str) -> Any:
        self.reads.append(key)
        return super().__getitem__(key)


def _spy(monkeypatch: pytest.MonkeyPatch) -> _EnvSpy:
    spy = _EnvSpy(os.environ)
    monkeypatch.setattr(os, "environ", spy)
    return spy


def _cfg(**kw: Any) -> config.ProjectConfig:
    return config.ProjectConfig(project="x", **kw)


class TestProviderKeyEnvIsUserDesignated:
    def test_default_variable_still_read_when_nothing_overrides_it(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-mine")
        assert config.resolve_anthropic_key(_cfg()) == "sk-ant-mine"

    def test_project_named_variable_is_refused_and_never_read(self, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", EVIL_ANTHROPIC)
        spy = _spy(monkeypatch)
        with pytest.raises(ConfigError) as ei:
            config.resolve_anthropic_key(_cfg(anthropic_key_env="AWS_SECRET_ACCESS_KEY"))
        assert "AWS_SECRET_ACCESS_KEY" not in spy.reads
        assert "AETHIS_ANTHROPIC_KEY_ENV" in str(ei.value)
        assert EVIL_ANTHROPIC not in str(ei.value)

    def test_project_variable_equal_to_users_designation_is_honoured(self, monkeypatch):
        monkeypatch.setenv("AETHIS_ANTHROPIC_KEY_ENV", "MY_KEY")
        monkeypatch.setenv("MY_KEY", "sk-ant-mine")
        assert config.resolve_anthropic_key(_cfg(anthropic_key_env="MY_KEY")) == "sk-ant-mine"

    def test_users_designation_applies_without_project_setting(self, monkeypatch):
        monkeypatch.setenv("AETHIS_ANTHROPIC_KEY_ENV", "MY_KEY")
        monkeypatch.setenv("MY_KEY", "sk-ant-mine")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-other")
        assert config.resolve_anthropic_key(_cfg()) == "sk-ant-mine"

    def test_project_variable_that_differs_from_designation_is_refused(self, monkeypatch):
        monkeypatch.setenv("AETHIS_ANTHROPIC_KEY_ENV", "MY_KEY")
        monkeypatch.setenv("MY_KEY", "sk-ant-mine")
        monkeypatch.setenv("OTHER", EVIL_ANTHROPIC)
        spy = _spy(monkeypatch)
        with pytest.raises(ConfigError):
            config.resolve_anthropic_key(_cfg(anthropic_key_env="OTHER"))
        assert "OTHER" not in spy.reads

    def test_deepseek_variable_follows_the_same_rule(self, monkeypatch):
        monkeypatch.setenv("EVIL", "ds-attacker")
        spy = _spy(monkeypatch)
        with pytest.raises(ConfigError):
            config.resolve_deepseek_key(_cfg(deepseek_key_env="EVIL"))
        assert "EVIL" not in spy.reads
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-mine")
        assert config.resolve_deepseek_key(_cfg()) == "ds-mine"
        monkeypatch.setenv("AETHIS_DEEPSEEK_KEY_ENV", "MY_DS")
        monkeypatch.setenv("MY_DS", "ds-designated")
        assert config.resolve_deepseek_key(_cfg(deepseek_key_env="MY_DS")) == "ds-designated"

    def test_hostile_anthropic_env_never_reaches_the_wire(self, net, _env, monkeypatch):
        _yaml(_env, anthropic_key_env="EVIL_VAR")
        _cache_default_key()
        monkeypatch.setenv("EVIL_VAR", EVIL_ANTHROPIC)
        result = runner.invoke(app, ["review", "proj_1", "--coach"])
        assert result.exit_code != 0
        _no_secret_on_wire(net, EVIL_ANTHROPIC)
        assert _requests(net) == []

    def test_designated_anthropic_key_is_sent_to_the_users_server_only(self, net, _env, monkeypatch):
        _cache_default_key()
        monkeypatch.setenv("AETHIS_ANTHROPIC_KEY_ENV", "MY_KEY")
        monkeypatch.setenv("MY_KEY", "sk-ant-mine")
        result = runner.invoke(app, ["review", "proj_1", "--coach"])
        reqs = _requests(net)
        assert reqs, _msg(result)
        assert {r.headers.get("x-anthropic-key") for r in reqs} == {"sk-ant-mine"}
        assert _hosts(net) == {"api.aethis.ai"}


class TestAethisKeyEnvIsUserDesignated:
    def test_project_named_variable_is_refused_even_with_a_cached_key(self, monkeypatch):
        _cache_default_key()
        monkeypatch.setenv("EVIL_VAR", "ak_attacker_chosen")
        spy = _spy(monkeypatch)
        with pytest.raises(ConfigError) as ei:
            config.resolve_api_key(_cfg(api_key_env="EVIL_VAR"))
        assert "EVIL_VAR" not in spy.reads
        assert "AETHIS_API_KEY_ENV" in str(ei.value)

    def test_project_named_variable_is_refused_with_no_cached_key(self, monkeypatch):
        monkeypatch.setenv("EVIL_VAR", "ak_attacker_chosen")
        spy = _spy(monkeypatch)
        with pytest.raises(ConfigError):
            config.resolve_api_key(_cfg(api_key_env="EVIL_VAR"))
        assert "EVIL_VAR" not in spy.reads

    def test_variable_equal_to_users_designation_is_honoured(self, monkeypatch):
        monkeypatch.setenv("AETHIS_API_KEY_ENV", "MY_AETHIS_KEY")
        monkeypatch.setenv("MY_AETHIS_KEY", "ak_mine")
        assert config.resolve_api_key(_cfg(api_key_env="MY_AETHIS_KEY")) == "ak_mine"

    def test_default_name_is_unchanged(self, monkeypatch):
        monkeypatch.setenv("AETHIS_API_KEY", "ak_env")
        assert config.resolve_api_key(_cfg()) == "ak_env"
        assert config.resolve_api_key(_cfg(api_key_env="AETHIS_API_KEY")) == "ak_env"

    def test_hostile_api_key_env_never_reaches_the_wire(self, net, _env, monkeypatch):
        _yaml(_env, api_key_env="EVIL_VAR")
        monkeypatch.setenv("EVIL_VAR", "ak_attacker_chosen")
        result = runner.invoke(app, ["review", "proj_1"])
        assert result.exit_code != 0
        assert _requests(net) == []


# --------------------------------------------------------------------------- #
# X-OpenAI-Key is never sent (regression guard: no code path exists today)
# --------------------------------------------------------------------------- #


class TestNoOpenAIKey:
    def test_no_openai_parameter_anywhere_on_the_client_surface(self):
        from aethis_cli import client as client_mod

        names = [n for n in inspect.signature(AethisClient.__init__).parameters]
        for _, fn in inspect.getmembers(AethisClient, inspect.isfunction):
            names += list(inspect.signature(fn).parameters)
        assert not [n for n in names if "openai" in n.lower()]
        assert "x-openai-key" not in inspect.getsource(client_mod).lower()

    def test_no_openai_header_on_the_wire_with_every_key_present(self, net, monkeypatch):
        _cache_default_key()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-mine")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-must-never-be-sent")
        runner.invoke(app, ["review", "proj_1", "--coach"])
        assert _requests(net)
        for r in _requests(net):
            assert not [h for h in r.headers if "openai" in h.lower()]
        _no_secret_on_wire(net, "sk-proj-must-never-be-sent")


# --------------------------------------------------------------------------- #
# ONE key resolution for every path: --api-key > designated env var > stored key
# --------------------------------------------------------------------------- #

KEY_COMMANDS = [
    ["whoami"],
    ["usage"],
    ["--output", "json", "status"],
    ["projects", "list"],
    ["rulesets", "list", "--project-id", "proj_1"],
    ["decide", "-b", "aethis/slug", "-i", "{}"],
    ["explain", "-b", "aethis/slug"],
    ["review", "proj_1"],
]


def _keys_sent(net: Any) -> set[str]:
    return {r.headers["x-api-key"] for r in _requests(net) if "x-api-key" in r.headers}


class TestOneKeyResolution:
    @pytest.mark.parametrize("args", KEY_COMMANDS)
    def test_designated_variable_beats_the_default_variable_on_every_path(self, net, monkeypatch, args):
        monkeypatch.setenv("AETHIS_API_KEY_ENV", "MY_API_KEY")
        monkeypatch.setenv("MY_API_KEY", "ak_designated")
        monkeypatch.setenv("AETHIS_API_KEY", "ak_default")
        runner.invoke(app, args)
        assert _keys_sent(net) == {"ak_designated"}, args
        _no_secret_on_wire(net, "ak_default")

    @pytest.mark.parametrize("args", KEY_COMMANDS)
    def test_unset_designated_variable_never_falls_back_to_the_default_variable(self, net, monkeypatch, args):
        monkeypatch.setenv("AETHIS_API_KEY_ENV", "MY_API_KEY")
        monkeypatch.setenv("AETHIS_API_KEY", "ak_default")
        runner.invoke(app, args)
        _no_secret_on_wire(net, "ak_default")

    def test_unset_designated_variable_falls_through_to_the_stored_key(self, net, monkeypatch):
        _cache_default_key()
        monkeypatch.setenv("AETHIS_API_KEY_ENV", "MY_API_KEY")
        monkeypatch.setenv("AETHIS_API_KEY", "ak_default")
        runner.invoke(app, ["whoami"])
        assert _keys_sent(net) == {CACHED}

    @pytest.mark.parametrize("args", KEY_COMMANDS)
    def test_api_key_flag_beats_every_variable(self, net, monkeypatch, args):
        monkeypatch.setenv("AETHIS_API_KEY_ENV", "MY_API_KEY")
        monkeypatch.setenv("MY_API_KEY", "ak_designated")
        monkeypatch.setenv("AETHIS_API_KEY", "ak_default")
        _cache_default_key()
        runner.invoke(app, ["--api-key", "ak_flag", *args])
        assert _keys_sent(net) == {"ak_flag"}, args

    def test_api_key_flag_beats_the_designated_variable_for_project_commands(self, net, _env, monkeypatch):
        _yaml(_env, api_key_env="MY_API_KEY")
        monkeypatch.setenv("AETHIS_API_KEY_ENV", "MY_API_KEY")
        monkeypatch.setenv("MY_API_KEY", "ak_env")
        runner.invoke(app, ["--api-key", "ak_flag", "review", "proj_1"])
        assert _keys_sent(net) == {"ak_flag"}

    def test_unit_precedence(self, monkeypatch):
        from aethis_cli.auth_helpers import resolve_cached_key

        _cache_default_key()
        monkeypatch.setenv("AETHIS_API_KEY", "ak_default")
        assert resolve_cached_key() == "ak_default"
        monkeypatch.setenv("AETHIS_API_KEY_ENV", "MY_API_KEY")
        assert resolve_cached_key() == CACHED
        monkeypatch.setenv("MY_API_KEY", "ak_designated")
        assert resolve_cached_key() == "ak_designated"
        RUNTIME.api_key_override = "ak_flag"
        assert resolve_cached_key() == "ak_flag"


# --------------------------------------------------------------------------- #
# The project's OWN base_url is compared, not the effective one
# --------------------------------------------------------------------------- #


class TestProjectServerComparedBeforeOverrides:
    @pytest.mark.parametrize("args", [["whoami"], ["review", "proj_1"], ["--output", "json", "status"]])
    def test_env_server_different_from_the_project_server_is_refused(self, net, _env, monkeypatch, args):
        _cache_default_key()
        _yaml(_env, base_url=ATTACKER)
        monkeypatch.setenv("AETHIS_BASE_URL", ENV_URL)
        result = runner.invoke(app, args)
        assert result.exit_code != 0
        assert _requests(net) == []
        assert "attacker" not in _msg(result)

    def test_profile_server_different_from_the_project_server_is_refused(self, net, _env):
        _staging()
        _yaml(_env, base_url=ATTACKER)
        result = runner.invoke(app, ["--profile", "staging", "whoami"])
        assert result.exit_code != 0 and _requests(net) == []

    def test_unit(self, _env, monkeypatch):
        _yaml(_env, base_url=ATTACKER)
        monkeypatch.setenv("AETHIS_BASE_URL", ENV_URL)
        with pytest.raises(ConfigError):
            config.authorize_credential_server()
        monkeypatch.setenv("AETHIS_BASE_URL", ATTACKER + "/")
        assert config.authorize_credential_server() == ATTACKER


# --------------------------------------------------------------------------- #
# Refuse a wrong save target BEFORE asking anything; canonical URL on the wire
# --------------------------------------------------------------------------- #


class TestInlineLoginDetails:
    def test_no_prompt_when_the_save_target_check_would_refuse(self, net, monkeypatch):
        config.set_profile("staging", base_url=STAGING)
        monkeypatch.setenv("AETHIS_BASE_URL", ENV_URL)
        with (
            patch("aethis_cli.auth_helpers._is_interactive", return_value=True),
            patch("builtins.input", return_value="y") as prompt,
            patch("aethis_cli.auth.authenticate_with_clerk", return_value="signin-token"),
        ):
            runner.invoke(app, ["--profile", "staging", "projects", "list"])
        prompt.assert_not_called()

    def test_run_browser_login_posts_to_the_canonical_server(self, net):
        from aethis_cli.commands.login_cmd import run_browser_login

        config.set_profile("staging", base_url=STAGING + "/")
        net.on("POST", f"{STAGING}/api/v1/keys/", httpx.Response(201, json={"full_key": "ak_live_x", "key_id": "k"}))
        with patch("aethis_cli.auth.authenticate_with_clerk", return_value="signin-token"):
            run_browser_login("HTTPS://Staging.Example.test/", profile="staging")
        posts = [r for r in _requests(net) if r.method == "POST"]
        assert [r.url.path for r in posts] == ["/api/v1/keys/"]


# --------------------------------------------------------------------------- #
# resolve_base_url_with_source keeps the true source
# --------------------------------------------------------------------------- #


class TestAnonymousSourceLabel:
    def test_profile_source_is_not_reported_as_yaml(self, _env):
        config.set_profile("default", base_url=STAGING)
        _yaml(_env)
        assert config.resolve_base_url_with_source() == (STAGING, "profile")

    def test_yaml_source_is_reported_as_yaml(self, _env):
        _yaml(_env, base_url=ATTACKER)
        assert config.resolve_base_url_with_source() == (ATTACKER, "yaml")


def test_run_browser_login_refuses_a_save_target_the_profile_does_not_name(net, monkeypatch):
    from aethis_cli.commands.login_cmd import run_browser_login

    config.set_profile("staging", base_url=STAGING)
    monkeypatch.setenv("AETHIS_BASE_URL", ENV_URL)
    with patch("aethis_cli.auth.authenticate_with_clerk", return_value="signin-token") as browser:
        with pytest.raises(ConfigError):
            run_browser_login(ENV_URL, profile="staging")
    browser.assert_not_called()
    assert _requests(net) == []


# --------------------------------------------------------------------------- #
# The anonymous profile sends no key, whatever the environment says
# --------------------------------------------------------------------------- #

ANON_COMMANDS = [
    ["whoami"],
    ["usage"],
    ["status"],
    ["--output", "json", "status"],
    ["projects", "list"],
    ["rulesets", "list", "--project-id", "proj_1"],
    ["decide", "-b", "aethis/slug", "-i", "{}"],
    ["explain", "-b", "aethis/slug"],
    ["review", "proj_1"],
]


class TestAnonymousProfileSendsNoKey:
    @pytest.fixture(autouse=True)
    def _keys_everywhere(self, monkeypatch):
        monkeypatch.setenv("AETHIS_API_KEY", "ak_secret")
        monkeypatch.setenv("AETHIS_API_KEY_ENV", "MY_API_KEY")
        monkeypatch.setenv("MY_API_KEY", "ak_designated")
        _cache_default_key()

    @pytest.mark.parametrize("args", ANON_COMMANDS)
    def test_flag(self, net, args):
        runner.invoke(app, ["--profile", "anonymous", *args])
        _no_secret_on_wire(net, "ak_secret", "ak_designated", CACHED)

    @pytest.mark.parametrize("args", ANON_COMMANDS)
    def test_env_profile(self, net, monkeypatch, args):
        monkeypatch.setenv("AETHIS_PROFILE", "anonymous")
        runner.invoke(app, args)
        _no_secret_on_wire(net, "ak_secret", "ak_designated", CACHED)

    @pytest.mark.parametrize("args", ANON_COMMANDS)
    def test_sticky_profile(self, net, args):
        config.set_active_profile("anonymous")
        runner.invoke(app, args)
        _no_secret_on_wire(net, "ak_secret", "ak_designated", CACHED)

    def test_unit(self):
        from aethis_cli.auth_helpers import resolve_cached_key

        RUNTIME.profile_override = "anonymous"
        assert resolve_cached_key() is None

    @pytest.mark.parametrize("args", [["whoami"], ["decide", "-b", "aethis/slug", "-i", "{}"], ["review", "proj_1"]])
    def test_api_key_flag_with_anonymous_profile_is_refused(self, net, args):
        result = runner.invoke(app, ["--profile", "anonymous", "--api-key", "ak_flag", *args])
        assert result.exit_code != 0
        assert _requests(net) == []
        assert "anonymous" in _msg(result).lower() and "--api-key" in _msg(result)


# --------------------------------------------------------------------------- #
# A project file that exists but is invalid is an error, never "no project"
# --------------------------------------------------------------------------- #

INVALID_PROJECTS = {
    "query": "project: x\nbase_url: 'https://staging.example/?tenant=x'\n",
    "userinfo": "project: x\nbase_url: 'https://u:p@staging.example'\n",
    "fragment": "project: x\nbase_url: 'https://staging.example/#f'\n",
    "not_a_string": "project: x\nbase_url: [a, b]\n",
    "no_project_key": "base_url: https://staging.example\n",
    "bad_yaml": "project: [unclosed\n",
    "not_a_mapping": "- a\n- b\n",
    "plain_mapping_no_project": "foo: bar\n",
    "scalar": "just a string\n",
    "empty_base_url": "project: x\nbase_url: ''\n",
    "null_base_url": "project: x\nbase_url:\n",
    "non_utf8": b"project: x\n# \xff\xfe\n",
}


def _write_project(path: Path, kind: str) -> None:
    data = INVALID_PROJECTS[kind]
    (path / "aethis.yaml").write_bytes(data if isinstance(data, bytes) else data.encode())


INVALID_PROJECT_COMMANDS = [
    ["decide", "-b", "aethis/slug", "-i", "{}"],
    ["explain", "-b", "aethis/slug"],
    ["fields", "-b", "aethis/slug"],
    ["status"],
    ["--output", "json", "status"],
    ["whoami"],
    ["usage"],
    ["rulesets", "list", "--public"],
    ["rulesets", "list"],
    ["rulebooks", "list"],
    ["projects", "list"],
    ["review", "proj_1"],
    ["cancel"],
]

# Phrases a command prints when it has quietly treated a broken project file as absent.
SILENT_FALLBACK_TEXT = ("no aethis.yaml", "no project id", "no project context", "showing public")


class TestInvalidProjectFileFailsLoud:
    @pytest.mark.parametrize("kind", sorted(INVALID_PROJECTS))
    @pytest.mark.parametrize("args", INVALID_PROJECT_COMMANDS)
    @pytest.mark.parametrize("keyed", [True, False])
    def test_command_fails_and_contacts_no_host(self, net, _env, kind, args, keyed):
        _write_project(_env / "project", kind)
        if keyed:
            _cache_default_key()
        result = runner.invoke(app, args)
        assert result.exit_code != 0, (kind, args, keyed, result.output)
        # The real error, not a command printing its own "no project" line and exiting 1.
        assert isinstance(result.exception, ConfigError), (kind, args, keyed, repr(result.exception))
        assert _requests(net) == [], (kind, args, keyed)
        text = _msg(result).lower()
        assert text.strip(), "the error must say something"
        assert not [t for t in SILENT_FALLBACK_TEXT if t in text], (kind, args, text)

    def test_no_project_file_still_means_no_project(self, net):
        result = runner.invoke(app, ["decide", "-b", "aethis/slug", "-i", "{}"])
        assert {r.url.host for r in _requests(net)} == {"api.aethis.ai"}, _msg(result)

    def test_status_names_the_problem_not_no_aethis_yaml(self, net, _env):
        (_env / "project" / "aethis.yaml").write_text(INVALID_PROJECTS["query"])
        result = runner.invoke(app, ["status"])
        assert "no aethis.yaml" not in _msg(result)


# --------------------------------------------------------------------------- #
# An undesignated project api_key_env is refused on EVERY key-resolving path
# --------------------------------------------------------------------------- #


class TestUndesignatedProjectApiKeyEnvRefusedEverywhere:
    @pytest.fixture(autouse=True)
    def _hostile(self, _env, monkeypatch):
        _yaml(_env, api_key_env="AWS_SECRET_ACCESS_KEY")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret-value")
        _cache_default_key()

    @pytest.mark.parametrize(
        "args",
        [
            ["projects", "list"],
            ["decide", "-b", "aethis/slug", "-i", "{}"],
            ["explain", "-b", "aethis/slug"],
            ["whoami"],
            ["usage"],
            ["status"],
            ["--output", "json", "status"],
            ["review", "proj_1"],
            ["rulesets", "list", "--project-id", "proj_1"],
        ],
    )
    def test_refused_and_never_read_or_sent(self, net, monkeypatch, args):
        spy = _spy(monkeypatch)
        result = runner.invoke(app, args)
        assert result.exit_code != 0, (args, result.output)
        assert "AWS_SECRET_ACCESS_KEY" not in spy.reads
        assert _requests(net) == []
        _no_secret_on_wire(net, "aws-secret-value")
        assert "AETHIS_API_KEY_ENV" in _msg(result)

    def test_designated_name_is_accepted_on_those_paths(self, net, monkeypatch):
        monkeypatch.setenv("AETHIS_API_KEY_ENV", "AWS_SECRET_ACCESS_KEY")
        runner.invoke(app, ["whoami"])
        assert _keys_sent(net) == {"aws-secret-value"}


# --------------------------------------------------------------------------- #
# AETHIS_BASE_URL outside a project gets the same structural rejection
# --------------------------------------------------------------------------- #


class TestEnvServerStructure:
    @pytest.mark.parametrize(
        "bad", ["https://u:p@staging.example", "https://staging.example?x=1", "https://staging.example#f"]
    )
    @pytest.mark.parametrize("args", [["rulesets", "list", "--public"], ["rulebooks", "list"]])
    def test_anonymous_reads_fail_loud(self, net, monkeypatch, bad, args):
        monkeypatch.setenv("AETHIS_BASE_URL", bad)
        result = runner.invoke(app, args)
        assert result.exit_code != 0
        assert _requests(net) == []
        assert "u:p" not in _msg(result)


class TestUnsafeUrlParts:
    @pytest.mark.parametrize(
        "bad", ["https://exämple.test", "https://staging.example/\x07x", "https://staging.example/a\x00b"]
    )
    def test_control_and_non_ascii_rejected(self, bad):
        with pytest.raises(ConfigError):
            config._reject_unsafe_url_parts(bad)

    @pytest.mark.parametrize("good", ["https://staging.example", "http://0.0.0.0:8080", "https://staging.example/v1"])
    def test_ordinary_urls_accepted(self, good):
        config._reject_unsafe_url_parts(good)

    def test_non_string_project_base_url_is_refused_for_credentials_too(self, net, _env):
        (_env / "project" / "aethis.yaml").write_text("project: x\nbase_url: [a, b]\n")
        _cache_default_key()
        with pytest.raises(ConfigError):
            config.authorize_credential_server()


class TestProjectFileReaders:
    @pytest.mark.parametrize("kind", ["bad_yaml", "not_a_mapping", "scalar", "non_utf8"])
    def test_unreadable_project_file_is_an_error_for_the_credential_checks(self, _env, kind):
        _write_project(_env / "project", kind)
        with pytest.raises(ConfigError):
            config.authorize_credential_server()
        with pytest.raises(ConfigError):
            config.check_project_api_key_env()

    def test_no_project_file_is_not_an_error_for_the_credential_checks(self):
        assert config.authorize_credential_server() == DEFAULT_BASE_URL
        config.check_project_api_key_env()


# --------------------------------------------------------------------------- #
# Round 4 review items
# --------------------------------------------------------------------------- #


class TestEmptyProjectBaseUrl:
    @pytest.mark.parametrize("kind", ["empty_base_url", "null_base_url"])
    def test_authorize_refuses_a_present_but_empty_base_url(self, _env, kind):
        _write_project(_env / "project", kind)
        with pytest.raises(ConfigError):
            config.authorize_credential_server()

    def test_absent_base_url_still_means_the_default(self, _env):
        _yaml(_env)
        assert config.authorize_credential_server() == DEFAULT_BASE_URL
        assert config.load_project_config().base_url == DEFAULT_BASE_URL


class TestUnreadableProjectFile:
    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
    @pytest.mark.parametrize("args", [["whoami"], ["decide", "-b", "aethis/slug", "-i", "{}"], ["status"]])
    def test_permission_error_is_a_clean_config_error(self, net, _env, args):
        _yaml(_env)
        path = _env / "project" / "aethis.yaml"
        path.chmod(0)
        try:
            result = runner.invoke(app, args)
        finally:
            path.chmod(0o600)
        assert isinstance(result.exception, ConfigError), repr(result.exception)
        assert str(path) in _msg(result)
        assert _requests(net) == []

    def test_yaml_error_text_does_not_quote_the_file(self, _env):
        (_env / "project" / "aethis.yaml").write_text("project: [ak_live_PASTED_SECRET\n")
        with pytest.raises(ConfigError) as ei:
            config.load_project_config()
        assert "ak_live_PASTED_SECRET" not in str(ei.value)
        with pytest.raises(ConfigError) as ei:
            config.authorize_credential_server()
        assert "ak_live_PASTED_SECRET" not in str(ei.value)


class TestStatusReportsTheRefusalWithoutAKey:
    @pytest.fixture(autouse=True)
    def _hostile_no_key(self, _env):
        _yaml(_env, base_url=ATTACKER)

    def test_human(self, net):
        result = runner.invoke(app, ["status", "--project-id", "proj_1"])
        assert result.exit_code == 1
        text = _msg(result)
        assert text.count("refused") >= 2  # identity and generation
        assert _requests(net) == []

    def test_json(self, net):
        result = runner.invoke(app, ["--output", "json", "status", "--project-id", "proj_1"])
        assert result.exit_code == 1
        body = json.loads(result.output)
        assert body["server"].get("refused")
        assert body["identity"].get("refused")
        assert body["generation"].get("refused")
        assert _requests(net) == []


class TestProfileBaseUrlStructure:
    @pytest.mark.parametrize("bad", ["https://alice:secret@attacker.example", "https://attacker.example?x=1"])
    @pytest.mark.parametrize("args", [["rulesets", "list", "--public"], ["rulebooks", "list"], ["projects", "list"]])
    def test_a_hand_edited_profile_url_is_never_used(self, net, bad, args):
        config.save_credentials(
            {"active_profile": "default", "profiles": {"default": {"api_key": CACHED, "base_url": bad}}}
        )
        result = runner.invoke(app, args)
        assert result.exit_code != 0
        assert _requests(net) == []
        assert "secret" not in _msg(result)

    @pytest.mark.parametrize("bad", ["https://alice:secret@attacker.example", "https://attacker.example#f"])
    def test_profile_add_refuses_it(self, bad):
        result = runner.invoke(app, ["profile", "add", "p", "--base-url", bad])
        assert result.exit_code != 0
        assert "p" not in config.load_credentials()["profiles"]
        assert "secret" not in _msg(result)


class TestRefusalTextShowsOriginOnly:
    def test_save_target_error_has_no_path(self):
        config.set_profile("staging", base_url=STAGING)
        with pytest.raises(ConfigError) as ei:
            config.check_save_target("https://env.example.test/tok_SECRET/x", "env", "staging", "login")
        text = str(ei.value)
        assert "env.example.test" in text and "tok_SECRET" not in text

    def test_project_server_refusal_names_the_file_not_the_url(self, _env):
        _yaml(_env, base_url=ATTACKER + "/tok_SECRET")
        with pytest.raises(ConfigError) as ei:
            config.authorize_credential_server()
        text = str(ei.value)
        assert str(_env / "project" / "aethis.yaml") in text and "tok_SECRET" not in text

    def test_env_name_refusal_names_the_file(self, _env):
        _yaml(_env, api_key_env="AWS_SECRET_ACCESS_KEY")
        with pytest.raises(ConfigError) as ei:
            config.check_project_api_key_env()
        assert str(_env / "project" / "aethis.yaml") in str(ei.value)
        assert "AWS_SECRET_ACCESS_KEY" not in str(ei.value)


class TestAnonymousProfileMessages:
    @pytest.mark.parametrize("cmd", ["whoami", "usage"])
    def test_say_the_anonymous_profile_is_active(self, net, monkeypatch, cmd):
        monkeypatch.setenv("AETHIS_API_KEY", "ak_secret")
        result = runner.invoke(app, ["--profile", "anonymous", cmd])
        text = _msg(result)
        assert result.exit_code == 1 and "anonymous" in text.lower()
        assert "set AETHIS_API_KEY" not in text
        assert _requests(net) == []


class TestAnonymousOrderingPinned:
    def test_anonymous_beats_the_api_key_override(self):
        from aethis_cli.auth_helpers import resolve_cached_key

        RUNTIME.profile_override = "anonymous"
        RUNTIME.api_key_override = "ak_x"
        assert resolve_cached_key() is None

    def test_inline_login_refuses_before_returning_the_override(self):
        from aethis_cli.auth_helpers import require_auth_or_login_inline
        from aethis_cli.errors import AuthRequired

        RUNTIME.profile_override = "anonymous"
        RUNTIME.api_key_override = "ak_x"
        with pytest.raises(AuthRequired):
            require_auth_or_login_inline()


class TestInitIsProfileSetup:
    """``aethis init`` sends nothing: a project file in a parent directory must not gate it."""

    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        monkeypatch.setenv("AETHIS_API_KEY", "ak_present")

    @pytest.mark.parametrize(
        "kind", ["other_server", "undesignated_key_env", "bad_yaml", "non_utf8", "scalar", "empty_base_url"]
    )
    def test_no_prompt_with_a_key_set_succeeds_under_any_parent_project(self, net, _env, kind):
        parent = {
            "other_server": f"project: x\nbase_url: {ATTACKER}\n",
            "undesignated_key_env": "project: x\napi_key_env: AWS_SECRET_ACCESS_KEY\n",
        }.get(kind)
        if parent is not None:
            (_env / "project" / "aethis.yaml").write_text(parent)
        else:
            _write_project(_env / "project", kind)
        result = runner.invoke(app, ["--no-prompt", "init", "newproj"])
        assert result.exit_code == 0, _msg(result)
        assert (Path.cwd() / "newproj" / "aethis.yaml").exists()
        assert _requests(net) == []

    def test_login_is_called_with_explicit_arguments(self, net, monkeypatch):
        monkeypatch.delenv("AETHIS_API_KEY")
        with patch("aethis_cli.commands.login_cmd.login") as login:
            result = runner.invoke(app, ["init", "newproj"])
        assert result.exit_code == 0, _msg(result)
        login.assert_called_once_with(api_key=None, timeout=120, profile=None)

    def test_real_login_command_accepts_those_arguments(self, net, monkeypatch):
        """The call init makes must not hand Typer OptionInfo defaults to the command."""
        from aethis_cli.commands import login_cmd

        monkeypatch.delenv("AETHIS_API_KEY")
        with patch.object(login_cmd, "run_browser_login", return_value="ak_live_x") as browser:
            result = runner.invoke(app, ["init", "newproj"])
        assert result.exit_code == 0, _msg(result)
        browser.assert_called_once()


class TestProfileBaseUrlStructureInsideAProject:
    @pytest.mark.parametrize("bad", ["https://alice:secret@attacker.example", "https://attacker.example#f"])
    @pytest.mark.parametrize("args", [["decide", "-b", "aethis/slug", "-i", "{}"], ["explain", "-b", "aethis/slug"]])
    def test_anonymous_reads_never_use_it(self, net, _env, bad, args):
        _yaml(_env)  # a project that names no server, so the profile's server applies
        config.save_credentials({"active_profile": "default", "profiles": {"default": {"base_url": bad}}})
        result = runner.invoke(app, args)
        assert result.exit_code != 0
        assert _requests(net) == []
        assert "secret" not in _msg(result)
