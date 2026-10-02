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


def _msg(result: Any) -> str:
    return (result.output or "") + str(result.exception or "")


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
            ["decide", "-b", "slug", "-i", "{}"],
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
        result = runner.invoke(app, ["--profile", "anonymous", "decide", "-b", "slug", "-i", "{}"])
        for r in _requests(net):
            assert "x-api-key" not in r.headers
            assert "authorization" not in r.headers
        assert result.exit_code in (0, 1)

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
