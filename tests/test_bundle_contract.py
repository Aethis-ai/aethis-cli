"""`aethis generate` / `aethis refine` send the project's bundle contract.

An author-declared ``contracts/bundle.yaml`` (input invariants) is sent to the
engine after the field spec and before the job starts, and the build is checked
to have captured it. The digest is computed by the server only; the CLI
compares the server's values with each other.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import respx
import typer
import yaml

from aethis_cli.client import AethisAPIError, AethisClient
from aethis_cli.commands import generate_cmd, refine_cmd

BASE = "http://engine.test"
DIGEST = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

CONTRACT = {
    "schema_version": 1,
    "input_invariants": [
        {
            "id": "end_not_before_start",
            "expr": {
                "type": "op",
                "operator": "<=",
                "args": [
                    {"type": "field_ref", "key": "period.start_date"},
                    {"type": "field_ref", "key": "period.end_date"},
                ],
            },
            "error_fields": ["period.end_date"],
            "message": "The end date must not be before the start date.",
        }
    ],
}


def _flat(capsys) -> str:
    return re.sub(r"\s+", " ", _ANSI.sub("", capsys.readouterr().out))


def _project(tmp_path, contract=None, raw: str | None = None):
    (tmp_path / "sources").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sources" / "a.md").write_text("the source document")
    if contract is not None or raw is not None:
        (tmp_path / "contracts").mkdir(exist_ok=True)
        (tmp_path / "contracts" / "bundle.yaml").write_text(raw if raw is not None else yaml.safe_dump(contract))
    return tmp_path


def _wire(monkeypatch, tmp_path, client) -> None:
    cfg = SimpleNamespace(config_path=tmp_path, base_url=BASE, project_id="proj_abc", project="p")
    monkeypatch.setattr(generate_cmd, "load_project_config", lambda: cfg)
    monkeypatch.setattr(generate_cmd, "resolve_api_key", lambda _cfg: "ak")
    monkeypatch.setattr(generate_cmd, "resolve_anthropic_key", lambda _cfg: None)
    monkeypatch.setattr(generate_cmd, "make_authed_client", lambda *_a, **_k: client)
    monkeypatch.setattr(generate_cmd.time, "sleep", lambda *_a, **_k: None)


def _engine(*, advertised, stored=None, put_digest=DIGEST, job_digest=DIGEST) -> MagicMock:
    client = MagicMock()
    client.list_sources.return_value = {"sources": []}
    client.upload_sources.return_value = {
        "new": 1,
        "reused": 0,
        "sources": [{"source_id": "src_1", "filename": "a.md", "reused": False}],
    }
    client.generate.return_value = {"job_id": "job_1"}
    client.last_rate_limit = None
    client.get_schema.return_value = {"fields": []}
    client.supports_bundle_contract.return_value = advertised
    client.get_project.return_value = {"bundle_contract_digest": stored}
    client.set_bundle_contract.return_value = {
        "bundle_contract_digest": put_digest,
        "bundle_contract_schema_version": 1,
    }
    job = {"status": "success", "progress_percent": 100}
    if job_digest is not None:
        job["bundle_contract_digest"] = job_digest
    client.get_status.return_value = {"job": job, "latest_ruleset_id": "rs_new"}
    return client


def _names(client) -> list[str]:
    return [c[0] for c in client.method_calls]


def _run(mode="fresh", **kw):
    generate_cmd._run_generate(project_id="proj_abc", poll=kw.pop("poll", True), timeout=30, mode=mode, **kw)


# --- present x advertised ---------------------------------------------------


@pytest.mark.parametrize("mode", ["fresh", "refine"])
def test_present_and_advertised_puts_it_before_the_job_and_prints_the_digest(tmp_path, monkeypatch, capsys, mode):
    _project(tmp_path, CONTRACT)
    client = _engine(advertised=True)
    _wire(monkeypatch, tmp_path, client)

    _run(mode)

    client.set_bundle_contract.assert_called_once_with("proj_abc", CONTRACT)
    names = _names(client)
    assert names.index("set_bundle_contract") < names.index("generate")
    assert DIGEST in _flat(capsys)


@pytest.mark.parametrize("mode", ["fresh", "refine"])
def test_present_but_not_advertised_stops_before_any_upload_or_job(tmp_path, monkeypatch, mode):
    for advertised in (False, None):
        _project(tmp_path, CONTRACT)
        client = _engine(advertised=advertised)
        _wire(monkeypatch, tmp_path, client)

        with pytest.raises(typer.Exit) as exc:
            _run(mode)

        assert exc.value.exit_code != 0
        for blocked in ("set_bundle_contract", "upload_sources", "add_tests", "generate", "create_project"):
            assert blocked not in _names(client), (advertised, blocked)


def test_refine_command_is_wired_to_the_same_path(tmp_path, monkeypatch):
    _project(tmp_path, CONTRACT)
    client = _engine(advertised=True)
    _wire(monkeypatch, tmp_path, client)

    refine_cmd.refine(
        model=None,
        thinking=None,
        hint=None,
        project_id="proj_abc",
        seed_ruleset_id=None,
        poll=True,
        timeout=30,
        acceptance_contract=None,
    )

    client.set_bundle_contract.assert_called_once_with("proj_abc", CONTRACT)


# --- absent x advertised ----------------------------------------------------


@pytest.mark.parametrize("mode", ["fresh", "refine"])
def test_absent_with_stored_contract_clears_it_and_says_so(tmp_path, monkeypatch, capsys, mode):
    _project(tmp_path)
    client = _engine(advertised=True, stored=DIGEST)
    _wire(monkeypatch, tmp_path, client)

    _run(mode, poll=False)

    client.set_bundle_contract.assert_called_once_with("proj_abc", {"schema_version": 1})
    assert "cleared" in _flat(capsys)
    names = _names(client)
    assert names.index("set_bundle_contract") < names.index("generate")


@pytest.mark.parametrize("mode", ["fresh", "refine"])
def test_absent_with_nothing_stored_sends_nothing(tmp_path, monkeypatch, mode):
    _project(tmp_path)
    client = _engine(advertised=True, stored=None)
    _wire(monkeypatch, tmp_path, client)

    _run(mode)

    client.set_bundle_contract.assert_not_called()
    client.generate.assert_called_once()


def test_absent_and_not_advertised_does_nothing(tmp_path, monkeypatch):
    for advertised in (False, None):
        _project(tmp_path)
        client = _engine(advertised=advertised)
        _wire(monkeypatch, tmp_path, client)

        _run()

        client.set_bundle_contract.assert_not_called()
        client.generate.assert_called_once()


# --- readback after polling -------------------------------------------------


def test_matching_job_digest_lets_the_run_finish_and_publish(tmp_path, monkeypatch):
    _project(tmp_path, CONTRACT)
    client = _engine(advertised=True, job_digest=DIGEST)
    _wire(monkeypatch, tmp_path, client)

    _run()

    client.publish.assert_called_once_with("proj_abc")


@pytest.mark.parametrize("job_digest", [OTHER, None])
def test_missing_or_different_job_digest_fails_and_does_not_publish(tmp_path, monkeypatch, capsys, job_digest):
    _project(tmp_path, CONTRACT)
    client = _engine(advertised=True, job_digest=job_digest)
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit) as exc:
        _run()

    assert exc.value.exit_code != 0
    client.publish.assert_not_called()
    assert "bundle contract" in _flat(capsys).lower()


def test_no_poll_prints_the_digest_for_later_checking(tmp_path, monkeypatch, capsys):
    _project(tmp_path, CONTRACT)
    client = _engine(advertised=True)
    _wire(monkeypatch, tmp_path, client)

    _run(poll=False)

    client.get_status.assert_not_called()
    assert DIGEST in _flat(capsys)


def test_server_response_without_a_digest_stops_before_the_job(tmp_path, monkeypatch):
    _project(tmp_path, CONTRACT)
    client = _engine(advertised=True, put_digest=None)
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit) as exc:
        _run()

    assert exc.value.exit_code != 0
    client.generate.assert_not_called()


# --- local shape check and engine refusal -----------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "- just\n- a list\n",
        "input_invariants: []\n",
        "schema_version: 2\n",
        "schema_version: true\n",
        "schema_version: 1\ninput_invariants: not-a-list\n",
        "schema_version: 1\ninput_invariants: [",
    ],
)
def test_malformed_file_is_refused_locally_before_any_call(tmp_path, monkeypatch, capsys, raw):
    _project(tmp_path, raw=raw)
    client = _engine(advertised=True)
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit) as exc:
        _run()

    assert exc.value.exit_code != 0
    assert client.method_calls == []
    assert "bundle.yaml" in _flat(capsys)


def test_engine_422_is_shown_and_stops_the_command(tmp_path, monkeypatch, capsys):
    _project(tmp_path, CONTRACT)
    client = _engine(advertised=True)
    client.set_bundle_contract.side_effect = AethisAPIError(422, "invariant end_not_before_start: unknown field")
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit) as exc:
        _run()

    assert exc.value.exit_code != 0
    client.generate.assert_not_called()
    assert "unknown field" in _flat(capsys)


# --- client -----------------------------------------------------------------


def _openapi(paths: dict) -> dict:
    return {"paths": paths}


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        ({"/api/v1/public/projects/{project_id}/bundle-contract": {"put": {}, "get": {}}}, True),
        ({"/api/v1/public/projects/{project_id}/bundle-contract": {"get": {}}}, False),
        ({"/api/v1/public/projects/{project_id}/tests": {"post": {}}}, False),
    ],
)
def test_probe_reads_the_openapi_paths(paths, expected):
    with respx.mock(base_url=BASE) as m:
        m.get("/openapi.json").mock(return_value=httpx.Response(200, json=_openapi(paths)))
        with AethisClient("key", BASE) as client:
            assert client.supports_bundle_contract() is expected


@pytest.mark.parametrize("response", [httpx.Response(500, text="oops"), httpx.Response(200, text="<html>"), None])
def test_probe_is_none_when_the_schema_cannot_be_read(response):
    with respx.mock(base_url=BASE) as m:
        route = m.get("/openapi.json")
        if response is None:
            route.mock(side_effect=httpx.ConnectError("down"))
        else:
            route.mock(return_value=response)
        with AethisClient("key", BASE) as client:
            assert client.supports_bundle_contract() is None


def test_set_bundle_contract_puts_the_body_unchanged():
    seen: dict = {}

    def put(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"bundle_contract_digest": DIGEST})

    with respx.mock(base_url=BASE) as m:
        m.put("/api/v1/public/projects/proj/bundle-contract").mock(side_effect=put)
        with AethisClient("key", BASE) as client:
            result = client.set_bundle_contract("proj", CONTRACT)

    assert seen["body"] == CONTRACT
    assert result["bundle_contract_digest"] == DIGEST
