"""Uploading `tests/scenarios.yaml` must be idempotent, or say that it is not.

`aethis generate` uploads the whole of `tests/scenarios.yaml` on every run.
The API only ever appended, so N authoring cycles left N copies of every test
case — silently, because duplicates do not error: they inflate the denominator
of every pass rate. A run reporting `151/185 passed` looked like a legitimate
result while a third of the suite was copies of itself.

The engine now accepts `replace`, which makes the upload idempotent. Not every
engine carries it yet, and an engine that does not carry it **ignores the
member rather than rejecting it** — so sending the flag blindly would restore
the duplication without a single error to notice. The upload therefore asks the
engine what it supports, and says out loud when the answer is "append".

The fake engine below is stateful on purpose: these tests assert what is left
on the server after three runs, which is the thing the bug was about.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest
import respx
import typer

from aethis_cli.client import AethisClient
from aethis_cli.commands import generate_cmd

BASE = "http://engine.test"

SCENARIOS = """\
tests:
  - name: eligible adult
    inputs:
      applicant.age: 25
    expect:
      outcome: eligible
  - name: too young
    inputs:
      applicant.age: 10
    expect:
      outcome: not_eligible
  - name: unknown age
    inputs: {}
    expect:
      outcome: undetermined
"""

N_CASES = 3


class FakeEngine:
    """An in-memory stand-in for the project's test-case store.

    Mirrors the engine: `replace=true` -> `$set` (and reports how many were
    removed); anything else -> `$push`. An engine WITHOUT the capability
    ignores an unknown body member rather than rejecting it, exactly as an
    unconfigured pydantic model does — which is why `bodies` is recorded and
    asserted on: a 422 would never arrive to warn anyone.
    """

    def __init__(
        self,
        *,
        supports_replace: bool,
        openapi_status: int = 200,
        extra_properties: tuple[str, ...] = (),
    ) -> None:
        self.store: list[dict] = []
        self.supports_replace = supports_replace
        self.openapi_status = openapi_status
        self.extra_properties = extra_properties
        self.bodies: list[dict] = []
        self.openapi_calls = 0

    def openapi(self, request: httpx.Request) -> httpx.Response:
        self.openapi_calls += 1
        if self.openapi_status != 200:
            return httpx.Response(self.openapi_status, json={"detail": "unavailable"})
        properties: dict = {"test_cases": {"type": "array"}}
        for name in self.extra_properties:
            properties[name] = {"type": "boolean", "default": False}
        if self.supports_replace:
            properties["replace"] = {"type": "boolean", "default": False}
        return httpx.Response(
            200,
            json={
                "info": {"version": "0.0.0-test"},
                "components": {"schemas": {"AddTestCaseRequest": {"properties": properties}}},
            },
        )

    def add_tests(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        cases = body["test_cases"]
        if body.get("replace") and self.supports_replace:
            removed = len(self.store)
            self.store = list(cases)
            return httpx.Response(201, json={"added": len(cases), "replaced": removed})
        # No capability, or replace not asked for: append, like the old engine.
        self.store.extend(cases)
        return httpx.Response(201, json={"added": len(cases), "replaced": 0})


def _mount(engine: FakeEngine, router) -> None:
    _mount_schema(engine, router)
    router.post("/api/v1/public/projects/proj_abc/tests").mock(side_effect=engine.add_tests)


def _mount_schema(engine: FakeEngine, router) -> None:
    router.get("/openapi.json").mock(side_effect=engine.openapi)


def _mount_uploads(engine: FakeEngine, router) -> None:
    router.post("/api/v1/public/projects/proj_abc/tests").mock(side_effect=engine.add_tests)


def _project(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "scenarios.yaml").write_text(SCENARIOS)
    return tmp_path


def _flat(capsys) -> str:
    """Rich wraps at the terminal width; assert on content, not on wrapping."""
    return re.sub(r"\s+", " ", capsys.readouterr().out)


# ---------------------------------------------------------------------------
# The originating problem: three runs, three copies
# ---------------------------------------------------------------------------


@respx.mock(base_url=BASE)
def test_three_runs_leave_one_copy_when_the_engine_supports_replace(respx_mock, tmp_path):
    engine = FakeEngine(supports_replace=True)
    _mount(engine, respx_mock)
    project_dir = _project(tmp_path)

    for _ in range(3):
        with AethisClient("ak", BASE) as client:
            generate_cmd._upload_test_cases(client, "proj_abc", project_dir)

    assert len(engine.store) == N_CASES, "three runs must leave one copy of the file, not three"
    assert all(body.get("replace") is True for body in engine.bodies)


@respx.mock(base_url=BASE)
def test_the_replaced_count_is_reported_so_an_overwrite_is_visible(respx_mock, tmp_path, capsys):
    engine = FakeEngine(supports_replace=True)
    _mount(engine, respx_mock)
    project_dir = _project(tmp_path)

    with AethisClient("ak", BASE) as client:
        generate_cmd._upload_test_cases(client, "proj_abc", project_dir)
    first = _flat(capsys)
    # Nothing was there to overwrite on the first run.
    assert "0 replaced" in first

    with AethisClient("ak", BASE) as client:
        generate_cmd._upload_test_cases(client, "proj_abc", project_dir)
    second = _flat(capsys)
    assert f"{N_CASES} replaced" in second, "the count of overwritten cases must be shown, not just the word"


# ---------------------------------------------------------------------------
# An engine without the capability: append, but never silently
# ---------------------------------------------------------------------------


@respx.mock(base_url=BASE)
def test_the_flag_is_not_sent_to_an_engine_that_does_not_advertise_it(respx_mock, tmp_path):
    engine = FakeEngine(supports_replace=False)
    _mount(engine, respx_mock)
    project_dir = _project(tmp_path)

    with AethisClient("ak", BASE) as client:
        generate_cmd._upload_test_cases(client, "proj_abc", project_dir)

    assert engine.bodies, "the upload must still happen"
    assert "replace" not in engine.bodies[0], (
        "an engine without the capability IGNORES the member rather than rejecting it, "
        "so sending it would restore the duplication with nothing to notice"
    )


@respx.mock(base_url=BASE)
def test_an_appending_engine_is_warned_about_in_words(respx_mock, tmp_path, capsys):
    engine = FakeEngine(supports_replace=False)
    _mount(engine, respx_mock)
    project_dir = _project(tmp_path)

    for _ in range(3):
        with AethisClient("ak", BASE) as client:
            generate_cmd._upload_test_cases(client, "proj_abc", project_dir)
    out = _flat(capsys).lower()

    # The duplication genuinely happens here — that is the engine's behaviour,
    # not something the CLI can fix. What the CLI must not do is hide it.
    assert len(engine.store) == 3 * N_CASES
    assert "append" in out
    assert "duplicate" in out or "copy" in out or "copies" in out


@respx.mock(base_url=BASE)
def test_an_unreadable_schema_is_unknown_not_supported(respx_mock, tmp_path, capsys):
    engine = FakeEngine(supports_replace=True, openapi_status=500)
    _mount(engine, respx_mock)
    project_dir = _project(tmp_path)

    with AethisClient("ak", BASE) as client:
        assert client.supports_test_replace() is None
        generate_cmd._upload_test_cases(client, "proj_abc", project_dir)

    out = _flat(capsys).lower()
    assert "replace" not in engine.bodies[0], "unknown is not supported"
    assert "append" in out


# ---------------------------------------------------------------------------
# The capability probe itself
# ---------------------------------------------------------------------------


@respx.mock(base_url=BASE)
def test_capability_probe_reads_the_engines_own_schema(respx_mock):
    engine = FakeEngine(supports_replace=True)
    _mount_schema(engine, respx_mock)
    with AethisClient("ak", BASE) as client:
        assert client.supports_test_replace() is True


@respx.mock(base_url=BASE)
def test_capability_probe_says_false_when_the_schema_lacks_the_member(respx_mock):
    engine = FakeEngine(supports_replace=False)
    _mount_schema(engine, respx_mock)
    with AethisClient("ak", BASE) as client:
        assert client.supports_test_replace() is False


@respx.mock(base_url=BASE)
def test_a_member_merely_containing_the_word_is_not_the_member(respx_mock):
    """Schemas grow. A future engine gaining `replace_all` (or any member the
    word `replace` is a substring of) must not read as this capability — the
    probe asks for the member, never for the word appearing somewhere."""
    engine = FakeEngine(supports_replace=False, extra_properties=("replace_all", "replacement_policy"))
    _mount_schema(engine, respx_mock)
    with AethisClient("ak", BASE) as client:
        assert client.supports_test_replace() is False


@respx.mock(base_url=BASE)
def test_capability_probe_is_asked_once_per_client(respx_mock):
    engine = FakeEngine(supports_replace=True)
    _mount_schema(engine, respx_mock)
    with AethisClient("ak", BASE) as client:
        client.supports_test_replace()
        client.supports_test_replace()
        client.supports_test_replace()
    assert engine.openapi_calls == 1


@respx.mock(base_url=BASE)
def test_a_malformed_schema_is_unknown_rather_than_a_crash(respx_mock):
    respx_mock.get("/openapi.json").mock(return_value=httpx.Response(200, json={"components": "not-a-dict"}))
    with AethisClient("ak", BASE) as client:
        assert client.supports_test_replace() is None


@respx.mock(base_url=BASE)
def test_a_non_json_schema_response_is_unknown_rather_than_a_crash(respx_mock):
    respx_mock.get("/openapi.json").mock(return_value=httpx.Response(200, text="<html>proxy error</html>"))
    with AethisClient("ak", BASE) as client:
        assert client.supports_test_replace() is None


# ---------------------------------------------------------------------------
# The client method
# ---------------------------------------------------------------------------


@respx.mock(base_url=BASE)
def test_add_tests_omits_replace_unless_asked(respx_mock):
    engine = FakeEngine(supports_replace=True)
    _mount_uploads(engine, respx_mock)
    cases = [{"name": "a", "field_values": {}, "expected_outcome": "eligible"}]
    with AethisClient("ak", BASE) as client:
        client.add_tests("proj_abc", cases)
        assert "replace" not in engine.bodies[-1]
        client.add_tests("proj_abc", cases, replace=True)
        assert engine.bodies[-1]["replace"] is True


# ---------------------------------------------------------------------------
# Existing behaviour that must survive
# ---------------------------------------------------------------------------


def test_no_scenarios_file_is_not_an_error(tmp_path):
    from unittest.mock import MagicMock

    client = MagicMock()
    generate_cmd._upload_test_cases(client, "proj_abc", tmp_path)
    client.add_tests.assert_not_called()


def test_invalid_yaml_still_exits_one(tmp_path):
    from unittest.mock import MagicMock

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "scenarios.yaml").write_text("tests: [\n  - unclosed")
    with pytest.raises(typer.Exit) as exc:
        generate_cmd._upload_test_cases(MagicMock(), "proj_abc", tmp_path)
    assert exc.value.exit_code == 1
