"""`aethis publish --name` and the optional `display_name:` key in aethis.yaml.

Both set the human-readable ruleset name sent on publish. Absent => the
request is byte-identical to before.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner


def _invoke(args):
    from aethis_cli.main import app

    return CliRunner().invoke(app, args, catch_exceptions=False, env={})


def _project(tmp_path, extra=""):
    (tmp_path / "aethis.yaml").write_text("project: test-project\napi_key_env: AETHIS_API_KEY\n" + extra)
    (tmp_path / ".aethis").mkdir()
    (tmp_path / ".aethis" / "state.json").write_text('{"project_id": "proj_test"}')


def _run(tmp_path, monkeypatch, args, extra=""):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    _project(tmp_path, extra)
    client = MagicMock()
    client.run_tests.return_value = {"passed": 1, "failed": 0, "errors": 0, "total": 1}
    client.publish.return_value = {"ruleset_id": "rs_x"}
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = _invoke(["publish", *args])
    assert result.exit_code == 0, result.output
    return client.publish.call_args.kwargs


def test_name_flag_is_sent(tmp_path, monkeypatch):
    assert _run(tmp_path, monkeypatch, ["--name", "Life in the UK"])["name"] == "Life in the UK"


def test_display_name_key_is_sent(tmp_path, monkeypatch):
    kw = _run(tmp_path, monkeypatch, [], extra="display_name: English language\n")
    assert kw["name"] == "English language"


def test_flag_overrides_yaml(tmp_path, monkeypatch):
    kw = _run(tmp_path, monkeypatch, ["--name", "Flag"], extra="display_name: Yaml\n")
    assert kw["name"] == "Flag"


def test_absent_means_no_name(tmp_path, monkeypatch):
    assert _run(tmp_path, monkeypatch, [])["name"] is None


def _body(**kw):
    from aethis_cli.client import AethisClient

    captured: dict = {}

    class _Stub(AethisClient):
        def _request(self, method, path, **k):
            captured["kwargs"] = k
            return {}

    _Stub(base_url="http://test.invalid", api_key="ak_test").publish("proj_x", **kw)
    return captured["kwargs"]


def test_client_sends_name_in_body():
    assert _body(name="Personal information")["json"] == {"name": "Personal information"}


def test_client_body_unchanged_without_name():
    assert "json" not in _body()
    assert _body(slug="a/b")["json"] == {"slug": "a/b"}
