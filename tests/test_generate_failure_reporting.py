"""The drift report has to survive the failure it was written to explain.

`aethis generate --poll` prints a pinned-vs-produced field diff after a
generation. The diff existed for the case where the model did not honour the
pin — and that is exactly the case it could not reach: the poll loop raised
`typer.Exit(1)` the moment a job reported `failed`, several frames below the
call that would have printed it. A failed generation therefore said only
"Generation failed", and the one diagnostic that could say *what* the model
produced never ran.

Two consequences follow from the same shape and are tested here alongside it:

- the recorded `ruleset_id` in `.aethis/state.json` is written only on the
  success path, so after a failure it still names an *earlier* generation and
  `aethis fields pull` syncs from that one without saying so;
- a produced enum with **no** members skipped the member comparison entirely,
  so a field whose members were all dropped printed
  "all N pinned field(s) were produced" — success, over the worst case.

The engine writes `result_ruleset_id` only on its success paths, so a failed
job may name no artefact at all, and one it does name may already have been
purged. The rule these tests pin down is therefore not "always diff": it is
**diff what this run produced, and say so plainly when there is nothing to
diff** — never quietly fall back on the previous ruleset.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import typer

from aethis_cli.commands import fields_cmd, generate_cmd
from aethis_cli.config import read_state, write_state
from aethis_cli.errors import AethisAPIError

# A pinned escape-hatch enum: the shape whose member set is load-bearing.
SENTINEL_FIELDS = (
    "fields:\n  - key: ref.defect_waived\n    type: enum\n    enum_values: [waived, waived_after_referee_contact]\n"
)

PINNED_MEMBERS = ["waived", "waived_after_referee_contact"]


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(capsys) -> str:
    """Rich wraps and colourises; assert on content, not on presentation.

    Style codes are stripped as well as whitespace collapsed: rich emits them
    into captured output whenever colour is forced in the environment
    (``FORCE_COLOR``), which turns a plain substring assertion into a failure
    about markup rather than about behaviour.
    """
    return re.sub(r"\s+", " ", _ANSI.sub("", capsys.readouterr().out))


def _project(tmp_path):
    """A project directory with one source file and one pinned enum field."""
    (tmp_path / "sources").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sources" / "a.md").write_text("the source document")
    (tmp_path / "fields").mkdir(parents=True, exist_ok=True)
    (tmp_path / "fields" / "fields.yaml").write_text(SENTINEL_FIELDS)
    return tmp_path


def _wire(monkeypatch, tmp_path, client) -> None:
    """Point `_run_generate` at a tmp project and a stand-in engine."""
    cfg = SimpleNamespace(
        config_path=tmp_path,
        base_url="http://engine.test",
        project_id="proj_abc",
        project="p",
    )
    monkeypatch.setattr(generate_cmd, "load_project_config", lambda: cfg)
    monkeypatch.setattr(generate_cmd, "resolve_api_key", lambda _cfg: "ak")
    monkeypatch.setattr(generate_cmd, "resolve_anthropic_key", lambda _cfg: None)
    monkeypatch.setattr(generate_cmd, "make_authed_client", lambda *_a, **_k: client)
    monkeypatch.setattr(generate_cmd.time, "sleep", lambda *_a, **_k: None)


def _engine(status_payload: dict, schema: dict | None = None, schema_error: Exception | None = None) -> MagicMock:
    client = MagicMock()
    client.list_sources.return_value = {"sources": []}
    client.upload_sources.return_value = {
        "new": 1,
        "reused": 0,
        "sources": [{"source_id": "src_1", "filename": "a.md", "reused": False}],
    }
    client.generate.return_value = {"job_id": "job_1"}
    client.last_rate_limit = None
    client.get_status.return_value = status_payload
    if schema_error is not None:
        client.get_schema.side_effect = schema_error
    else:
        client.get_schema.return_value = schema or {"fields": []}
    return client


def _run(timeout: int = 30):
    return generate_cmd._run_generate(project_id="proj_abc", poll=True, timeout=timeout)


# ---------------------------------------------------------------------------
# Defect 1 — the diff is unreachable on the failure path
# ---------------------------------------------------------------------------


def test_a_failed_job_prints_the_drift_report_before_exiting(tmp_path, monkeypatch, capsys):
    """The originating problem: a failure that produced a draft must still be diffed."""
    _project(tmp_path)
    client = _engine(
        {"job": {"status": "failed", "error_message": "boom", "result_ruleset_id": "rs_from_this_run"}},
        schema={
            "fields": [
                {
                    "field_id": "ref.defect_waived",
                    # the padded member set the pin existed to exclude
                    "enum_values": [*PINNED_MEMBERS, "not_reviewed"],
                }
            ]
        },
    )
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit) as exc:
        _run()

    assert exc.value.exit_code == 1, "a failed generation still exits non-zero"
    out = _flat(capsys)
    assert "Generation failed" in out
    assert "not_reviewed" in out, "the drift report must reach the user on the failure path"
    assert client.get_schema.call_args[0][0] == "rs_from_this_run", (
        "the diff must be computed against the artefact THIS run produced"
    )


def test_a_failed_job_with_no_artefact_says_so_and_diffs_nothing(tmp_path, monkeypatch, capsys):
    """No identifier on the job: say it, and never fall back on the earlier ruleset."""
    _project(tmp_path)
    write_state(tmp_path, {"ruleset_id": "rs_from_an_earlier_run"})
    client = _engine(
        {
            "job": {"status": "failed", "error_message": "boom"},
            # the project's previous ruleset is right there in the payload — and
            # is exactly what must NOT be diffed
            "latest_ruleset_id": "rs_from_an_earlier_run",
        }
    )
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit):
        _run()

    out = _flat(capsys)
    assert client.get_schema.call_count == 0, (
        "the earlier ruleset is in reach — on disk and in the status payload — and must not be read"
    )
    assert "no ruleset" in out.lower(), "the absence of an artefact must be stated, not left silent"
    assert "all 1 pinned field(s) were produced" not in out, "nothing was compared, so nothing may be called clean"


def test_a_purged_artefact_is_reported_rather_than_diffed(tmp_path, monkeypatch, capsys):
    """The job names a draft, but it is gone (aethis-core#420): say so."""
    _project(tmp_path)
    client = _engine(
        {"job": {"status": "failed", "error_message": "boom", "result_ruleset_id": "rs_purged"}},
        schema_error=AethisAPIError(404, "Ruleset not found"),
    )
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit):
        _run()

    out = _flat(capsys)
    assert "rs_purged" in out, "the artefact that could not be read must be named"
    assert "all 1 pinned field(s) were produced" not in out, "an unread schema is not a clean one"


# ---------------------------------------------------------------------------
# Defect 2 — a failure leaves a stale pointer on disk
# ---------------------------------------------------------------------------


def test_a_failure_clears_the_recorded_ruleset_id(tmp_path, monkeypatch, capsys):
    _project(tmp_path)
    write_state(tmp_path, {"ruleset_id": "rs_from_an_earlier_run"})
    client = _engine({"job": {"status": "failed", "error_message": "boom"}})
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit):
        _run()

    assert not read_state(tmp_path).get("ruleset_id"), (
        "the recorded id names an earlier generation, so 'fields pull' must not silently use it"
    )
    assert "rs_from_an_earlier_run" in _flat(capsys), "the id that was cleared must be named so it can be recovered"


def test_after_a_failure_fields_pull_refuses_instead_of_syncing_the_earlier_ruleset(tmp_path, monkeypatch, capsys):
    """The reporter-facing loop: the next command must not quietly use the old ruleset."""
    _project(tmp_path)
    write_state(tmp_path, {"ruleset_id": "rs_from_an_earlier_run"})
    client = _engine({"job": {"status": "failed", "error_message": "boom"}})
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit):
        _run()
    capsys.readouterr()

    pull_client = MagicMock()
    cfg = SimpleNamespace(config_path=tmp_path, base_url="http://engine.test", project_id="proj_abc", project="p")
    monkeypatch.setattr(fields_cmd, "load_project_config", lambda: cfg)
    monkeypatch.setattr(fields_cmd, "resolve_api_key", lambda _cfg: "ak")
    monkeypatch.setattr(fields_cmd, "make_authed_client", lambda *_a, **_k: pull_client)

    with pytest.raises(typer.Exit) as exc:
        fields_cmd.pull(ruleset_id=None)

    assert exc.value.exit_code == 1
    assert pull_client.get_schema.call_count == 0, "fields pull must not read the earlier ruleset's schema"


def test_a_first_run_failure_records_no_ruleset_pointer(tmp_path, monkeypatch, capsys):
    """No prior state at all: nothing to clear, and nothing invented."""
    _project(tmp_path)
    client = _engine({"job": {"status": "failed", "error_message": "boom"}})
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit):
        _run()

    assert not read_state(tmp_path).get("ruleset_id")
    assert "no ruleset" in _flat(capsys).lower()


def test_a_timeout_keeps_the_recorded_id_but_names_it_as_stale(tmp_path, monkeypatch, capsys):
    """A timeout is not a failed job — the run may still land — so the id stands.

    What must not stand is silence about it: the recorded id describes an
    earlier generation, and `fields pull` would use it.
    """
    _project(tmp_path)
    write_state(tmp_path, {"ruleset_id": "rs_from_an_earlier_run"})
    client = _engine({"job": {"status": "running", "progress_percent": 40}})
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit) as exc:
        _run(timeout=0)

    assert exc.value.exit_code == 1
    assert read_state(tmp_path).get("ruleset_id") == "rs_from_an_earlier_run", (
        "the job may still be running; the last known good id is not invalidated by a client-side timeout"
    )
    out = _flat(capsys)
    assert "Timed out" in out
    assert "rs_from_an_earlier_run" in out, "the stale pointer must be named rather than left to be discovered"


def test_a_success_still_records_the_ruleset_id_and_diffs_it(tmp_path, monkeypatch, capsys):
    """The success path is unchanged: state written, diff computed against this run."""
    _project(tmp_path)
    client = _engine(
        {"job": {"status": "success"}, "latest_ruleset_id": "rs_new"},
        schema={"fields": [{"field_id": "ref.defect_waived", "enum_values": PINNED_MEMBERS}]},
    )
    _wire(monkeypatch, tmp_path, client)

    _run()

    assert read_state(tmp_path)["ruleset_id"] == "rs_new"
    assert client.get_schema.call_args[0][0] == "rs_new"
    assert "all 1 pinned field(s) were produced" in _flat(capsys)


def test_a_success_prefers_the_jobs_own_ruleset_over_the_projects_newest(tmp_path, monkeypatch, capsys):
    """`latest_ruleset_id` is a property of the PROJECT, not of this job.

    Two generations running against one project (the aethis-core#420 shape)
    make the project's newest ruleset somebody else's artefact. Where the job
    names its own, that is the only honest answer — for the diff and for the
    id written to disk.
    """
    _project(tmp_path)
    client = _engine(
        {
            "job": {"status": "success", "result_ruleset_id": "rs_this_job"},
            "latest_ruleset_id": "rs_a_concurrent_run",
        },
        schema={"fields": [{"field_id": "ref.defect_waived", "enum_values": PINNED_MEMBERS}]},
    )
    _wire(monkeypatch, tmp_path, client)

    _run()

    assert client.get_schema.call_args[0][0] == "rs_this_job", "the diff must not bind another run's artefact"
    assert read_state(tmp_path)["ruleset_id"] == "rs_this_job", (
        "the recorded id is what every later command defaults to; it must name this job's ruleset"
    )


def test_a_success_falls_back_to_the_projects_newest_when_the_job_names_none(tmp_path, monkeypatch, capsys):
    """An engine that records no id on the job still has to yield something usable."""
    _project(tmp_path)
    client = _engine(
        {"job": {"status": "success"}, "latest_ruleset_id": "rs_new"},
        schema={"fields": [{"field_id": "ref.defect_waived", "enum_values": PINNED_MEMBERS}]},
    )
    _wire(monkeypatch, tmp_path, client)

    _run()

    assert read_state(tmp_path)["ruleset_id"] == "rs_new"


def test_a_success_repolled_for_an_id_reads_the_jobs_own(tmp_path, monkeypatch, capsys):
    """The re-poll for a late id must prefer the job's field too, not only the project's."""
    _project(tmp_path)
    client = _engine({"job": {"status": "success"}})
    client.get_status.side_effect = [
        {"job": {"status": "success"}},  # success reported before any id is populated
        {"job": {"status": "success", "result_ruleset_id": "rs_late"}},
    ]
    client.get_schema.return_value = {"fields": [{"field_id": "ref.defect_waived", "enum_values": PINNED_MEMBERS}]}
    _wire(monkeypatch, tmp_path, client)

    _run()

    assert read_state(tmp_path)["ruleset_id"] == "rs_late"
    assert client.get_schema.call_args[0][0] == "rs_late"


def test_a_mid_poll_api_error_names_the_stale_pointer(tmp_path, monkeypatch, capsys):
    """The sixth ending: the poll itself errors out.

    Like a timeout, nothing has been ruled — the job may well have succeeded —
    so the recorded id stands. What must not stand is silence about it: it
    names an earlier generation and `aethis fields pull` would use it.
    """
    _project(tmp_path)
    write_state(tmp_path, {"ruleset_id": "rs_from_an_earlier_run"})
    client = _engine({"job": {"status": "running", "progress_percent": 10}})
    client.get_status.side_effect = AethisAPIError(500, "upstream unavailable")
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit) as exc:
        _run()

    assert exc.value.exit_code == 1
    assert read_state(tmp_path).get("ruleset_id") == "rs_from_an_earlier_run", (
        "an API error rules nothing out; the last known good id is not invalidated by it"
    )
    assert "rs_from_an_earlier_run" in _flat(capsys), "the stale pointer must be named on this ending too"


# ---------------------------------------------------------------------------
# Defect 4 (#122) — the same ending, reached by the exception that actually
# interrupts a long poll
# ---------------------------------------------------------------------------
#
# The guard above is correct and, on the failure most likely to trigger it, it
# never ran. A dropped connection, a DNS failure, a TLS error or a read timeout
# raises `httpx.HTTPError`, not `AethisAPIError`: `AethisClient._request` wraps
# only HTTP *status* failures, so a transport failure unwinds past this
# handler to the top-level boundary in `main.py`, which renders one line and
# exits. `_invalidate_stale_pointer` is skipped, so the id is kept — right —
# and never named — wrong, and silently so.
#
# The tests below pin the whole path, not just the reported ending: a blip that
# the poll can simply survive should not end the run at all.


def test_a_mid_poll_transport_failure_names_the_stale_pointer(tmp_path, monkeypatch, capsys):
    """The reported defect: the guard must cover the transport error too.

    The twin of `test_a_mid_poll_api_error_names_the_stale_pointer`. Nothing has
    been ruled — the job may well have succeeded — so the id stands and must be
    named, and the recovery is one command away, so the warning carries it.
    """
    _project(tmp_path)
    write_state(tmp_path, {"ruleset_id": "rs_from_an_earlier_run"})
    client = _engine({"job": {"status": "running", "progress_percent": 10}})
    client.get_status.side_effect = httpx.RemoteProtocolError("Server disconnected without sending a response.")
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit) as exc:
        _run()

    assert exc.value.exit_code == 1
    assert read_state(tmp_path).get("ruleset_id") == "rs_from_an_earlier_run", (
        "a transport failure rules nothing out; the last known good id is not invalidated by it"
    )
    out = _flat(capsys)
    assert "rs_from_an_earlier_run" in out, "the stale pointer must be named on this ending too"
    assert "projects show" in out, "the warning must carry the command that recovers the real id"


def test_a_blip_mid_poll_does_not_end_the_generation(tmp_path, monkeypatch, capsys):
    """Better than naming the stale id: don't strand the run in the first place.

    A single dropped connection is the common form of this incident, and the
    job behind it usually finishes. Absorbing the blip turns the whole class
    from "silent stale pointer" into "no incident".
    """
    _project(tmp_path)
    write_state(tmp_path, {"ruleset_id": "rs_from_an_earlier_run"})
    client = _engine({"job": {"status": "running", "progress_percent": 10}})
    client.get_status.side_effect = [
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        {"job": {"status": "success", "result_ruleset_id": "rs_from_this_run"}},
    ]
    _wire(monkeypatch, tmp_path, client)

    _run()

    assert read_state(tmp_path)["ruleset_id"] == "rs_from_this_run", (
        "the poll survived the blip, so this run's id must be the one recorded"
    )


def test_a_blip_in_the_post_success_repoll_still_records_the_id(tmp_path, monkeypatch, capsys):
    """The seam that is strictly worse than the reported one.

    After the engine reports success the loop re-polls for the id. A transport
    failure *there* strands a generation the CLI already knows succeeded.
    """
    _project(tmp_path)
    write_state(tmp_path, {"ruleset_id": "rs_from_an_earlier_run"})
    client = _engine({"job": {"status": "running", "progress_percent": 10}})
    client.get_status.side_effect = [
        {"job": {"status": "success"}},  # success, id not yet populated
        httpx.ReadTimeout("timed out"),  # the re-poll blips
        {"job": {"status": "success", "result_ruleset_id": "rs_from_this_run"}},
    ]
    _wire(monkeypatch, tmp_path, client)

    _run()

    assert read_state(tmp_path)["ruleset_id"] == "rs_from_this_run", (
        "a blip while waiting for the id must not discard an id the next attempt returns"
    )


def test_a_transport_failure_on_publish_still_reports_the_ruleset(tmp_path, monkeypatch, capsys):
    """Publishing is the last step, and its failure is not the generation's.

    The id was recorded before the publish. Reporting only "Could not reach the
    Aethis API" would have the author read a successful generation as a failed
    one, and re-run it.
    """
    _project(tmp_path)
    client = _engine({"job": {"status": "success", "result_ruleset_id": "rs_from_this_run"}})
    client.publish.side_effect = httpx.ConnectError("connection refused")
    _wire(monkeypatch, tmp_path, client)

    _run()

    out = _flat(capsys)
    assert "rs_from_this_run" in out, "the generation succeeded and its id must be reported"
    assert "aethis publish" in out, "the un-run step must be named so it can be run"
    assert read_state(tmp_path)["ruleset_id"] == "rs_from_this_run"


def test_a_success_that_never_surfaces_an_id_names_the_stale_pointer(tmp_path, monkeypatch, capsys):
    """A success with no id leaves the same silent stale pointer.

    The id is deliberately not clobbered (nothing better is known), so this
    ending has exactly the shape the guard exists for: `fields pull` would sync
    from the earlier generation with nothing said.
    """
    _project(tmp_path)
    write_state(tmp_path, {"ruleset_id": "rs_from_an_earlier_run"})
    client = _engine({"job": {"status": "success"}})
    _wire(monkeypatch, tmp_path, client)

    _run()

    assert read_state(tmp_path)["ruleset_id"] == "rs_from_an_earlier_run", "a success never clobbers a prior good id"
    assert "rs_from_an_earlier_run" in _flat(capsys), (
        "a success that produced no id must still name the pointer it did not replace"
    )


def test_a_success_whose_field_diff_loses_the_connection_is_still_a_success(tmp_path, monkeypatch, capsys):
    """The guard must never fire on the run that recorded the pointer.

    The field diff runs *after* the new id is written. A transport failure
    there once escaped to the stale-pointer guard, so a generation that had
    succeeded and recorded exactly the right id announced "Done! Ruleset: X"
    and then insisted X was from an earlier generation — a false claim about
    the one fact the guard exists to keep honest, and the same shape of defect
    as the one it was written to fix.
    """
    _project(tmp_path)
    write_state(tmp_path, {"ruleset_id": "rs_from_an_earlier_run"})
    client = _engine({"job": {"status": "success", "result_ruleset_id": "rs_from_this_run"}})
    client.get_schema.side_effect = httpx.ConnectError("connection reset")
    _wire(monkeypatch, tmp_path, client)

    _run()  # must not raise: a diff that cannot be fetched is not a failed generation

    out = _flat(capsys)
    assert read_state(tmp_path)["ruleset_id"] == "rs_from_this_run", "this run's id was recorded and must stand"
    assert "from an earlier generation" not in out, (
        "the pointer this run just wrote is not stale, and must never be called stale"
    )


def test_the_guard_never_calls_this_runs_own_pointer_stale(tmp_path, capsys):
    """The invariant directly, independent of the route that reaches it."""
    write_state(tmp_path, {"ruleset_id": "rs_from_this_run", "project_id": "proj_abc"})

    generate_cmd._invalidate_stale_pointer(tmp_path, "error", produced_id="rs_from_this_run")

    assert "from an earlier generation" not in _flat(capsys), "the id this run produced is not an earlier one"
    assert read_state(tmp_path)["ruleset_id"] == "rs_from_this_run"


def test_a_persistent_outage_is_reported_as_unreachable_not_as_a_timeout(tmp_path, monkeypatch, capsys):
    """The blip budget has to be *bounded*, and nothing else asserted that.

    Without this the budget is a free variable: raise it and the poll simply
    absorbs a real outage until the deadline, then reports "Timed out" — which
    reads as a slow job and sends the author looking in the wrong place. Every
    other assertion here stays green under that mutation, because the timeout
    ending also names the stale pointer.
    """
    _project(tmp_path)
    write_state(tmp_path, {"ruleset_id": "rs_from_an_earlier_run"})
    client = _engine({"job": {"status": "running", "progress_percent": 10}})
    client.get_status.side_effect = httpx.ConnectError("connection refused")
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit):
        _run(timeout=5)

    out = _flat(capsys)
    assert "Timed out" not in out, "a dead connection is not a slow job and must not be reported as one"
    assert client.get_status.call_count == generate_cmd._TRANSPORT_BLIP_BUDGET + 1, (
        "the poll must give up after the budget, not grind on to the deadline"
    )


def test_a_lossy_but_working_connection_still_completes(tmp_path, monkeypatch, capsys):
    """The regression a total blip cap caused, pinned so it cannot come back.

    A flapping link (fail, ok, fail, ok …) never trips the *consecutive* bound,
    and it must not be aborted by any other bound either: the job behind it is
    progressing, and killing it one poll short of success delivers exactly the
    #122 experience — run dead, pointer naming an earlier generation — from the
    code written to prevent it. An absolute cap got this wrong because its
    tolerance shrinks as a job gets longer, which is backwards.
    """
    _project(tmp_path)
    write_state(tmp_path, {"ruleset_id": "rs_from_an_earlier_run"})
    running = {"job": {"status": "running", "progress_percent": 10}}
    # 20 dropped connections spread across the poll, never 3 in a row.
    steps: list = []
    for _ in range(20):
        steps.extend([httpx.ConnectError("flap"), running])
    steps.append({"job": {"status": "success", "result_ruleset_id": "rs_from_this_run"}})
    client = _engine(running)
    client.get_status.side_effect = steps
    _wire(monkeypatch, tmp_path, client)

    _run(timeout=600)

    assert read_state(tmp_path)["ruleset_id"] == "rs_from_this_run", (
        "a lossy link that keeps working must reach the success it was heading for"
    )


def test_an_absorbed_blip_leaves_a_durable_trace(tmp_path, monkeypatch, capsys):
    """The progress description is overwritten and invisible off a TTY.

    An absorbed blip is the event that used to strand generations, so it should
    leave a line worth grepping rather than only a spinner frame.
    """
    _project(tmp_path)
    client = _engine({"job": {"status": "running", "progress_percent": 10}})
    client.get_status.side_effect = [
        httpx.ConnectError("flap"),
        {"job": {"status": "success", "result_ruleset_id": "rs_from_this_run"}},
    ]
    _wire(monkeypatch, tmp_path, client)

    _run()

    assert "Lost the connection" in _flat(capsys), "an absorbed blip must be visible after the fact"


def test_a_timeout_after_blips_says_the_network_may_be_the_cause(tmp_path, monkeypatch, capsys):
    """Otherwise a flapping link is indistinguishable from a slow job.

    This is what the removed total-blip cap was really for, delivered without
    aborting anybody's run to get it.
    """
    _project(tmp_path)
    running = {"job": {"status": "running", "progress_percent": 10}}
    client = _engine(running)

    # An endless alternation rather than a fixed list: a list long enough for
    # the deadline is a guess, and when the guess is short the test reds with
    # StopIteration — passing for a reason it does not claim.
    state = {"n": 0}

    def flap(_pid):
        state["n"] += 1
        if state["n"] % 2:
            raise httpx.ConnectError("flap")
        return running

    client.get_status.side_effect = flap
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit):
        _run(timeout=5)

    out = _flat(capsys)
    assert "Timed out" in out
    assert "network problem" in out, "a poll full of dropped connections is not a slow job"


def test_a_failed_publish_says_why(tmp_path, monkeypatch, capsys):
    """A green tick over a ruleset that was never activated, with no reason.

    The generation did succeed, so it is reported as one — but silence about
    the publish leaves this ending indistinguishable from the deliberate
    `--no-publish` one, and the author is not told why the ruleset is inactive.
    """
    _project(tmp_path)
    client = _engine({"job": {"status": "success", "result_ruleset_id": "rs_from_this_run"}})
    client.publish.side_effect = httpx.ConnectError("connection refused")
    _wire(monkeypatch, tmp_path, client)

    _run()

    out = _flat(capsys)
    assert "Publish did not run" in out, "the step that failed must be named"
    assert "connection refused" in out, "and so must the reason"


def test_a_success_that_never_surfaces_an_id_diffs_nothing(tmp_path, monkeypatch, capsys):
    """A success with no id must not be diffed against the previous ruleset either."""
    _project(tmp_path)
    write_state(tmp_path, {"ruleset_id": "rs_from_an_earlier_run"})
    client = _engine({"job": {"status": "success"}})
    _wire(monkeypatch, tmp_path, client)

    _run()

    client.get_schema.assert_not_called()
    assert read_state(tmp_path)["ruleset_id"] == "rs_from_an_earlier_run", "a success never clobbers a prior good id"


# ---------------------------------------------------------------------------
# Defect 3 — an emptied enum, and a swallowed fetch error
# ---------------------------------------------------------------------------


def test_an_emptied_enum_is_drift_not_success(tmp_path, capsys):
    """Every member dropped is the worst case, and it printed success."""
    (tmp_path / "fields").mkdir(parents=True, exist_ok=True)
    (tmp_path / "fields" / "fields.yaml").write_text(SENTINEL_FIELDS)
    client = MagicMock()
    client.get_schema.return_value = {"fields": [{"field_id": "ref.defect_waived", "enum_values": []}]}

    generate_cmd._report_field_diff(client, "rs_1", tmp_path)

    out = _flat(capsys)
    assert "all 1 pinned field(s) were produced" not in out, "a field with no members left is not a clean production"
    assert "ref.defect_waived" in out
    for member in PINNED_MEMBERS:
        assert member in out, f"the dropped member {member} must be named"


def test_a_produced_field_that_is_no_longer_an_enum_is_drift(tmp_path, capsys):
    """The same shape arriving as a missing key rather than an empty list."""
    (tmp_path / "fields").mkdir(parents=True, exist_ok=True)
    (tmp_path / "fields" / "fields.yaml").write_text(SENTINEL_FIELDS)
    client = MagicMock()
    client.get_schema.return_value = {"fields": [{"field_id": "ref.defect_waived", "type": "string"}]}

    generate_cmd._report_field_diff(client, "rs_1", tmp_path)

    out = _flat(capsys)
    assert "all 1 pinned field(s) were produced" not in out
    assert "ref.defect_waived" in out


def test_a_schema_fetch_failure_is_surfaced_not_swallowed(tmp_path, capsys):
    (tmp_path / "fields").mkdir(parents=True, exist_ok=True)
    (tmp_path / "fields" / "fields.yaml").write_text(SENTINEL_FIELDS)
    client = MagicMock()
    client.get_schema.side_effect = AethisAPIError(500, "engine on fire")

    generate_cmd._report_field_diff(client, "rs_1", tmp_path)

    out = _flat(capsys)
    assert out.strip(), "a fetch failure must not be silent"
    assert "rs_1" in out
    assert "all 1 pinned field(s) were produced" not in out


def test_no_ruleset_id_says_nothing_was_compared(tmp_path, capsys):
    (tmp_path / "fields").mkdir(parents=True, exist_ok=True)
    (tmp_path / "fields" / "fields.yaml").write_text(SENTINEL_FIELDS)
    client = MagicMock()

    generate_cmd._report_field_diff(client, None, tmp_path)

    out = _flat(capsys)
    client.get_schema.assert_not_called()
    assert "no ruleset" in out.lower()


def test_nothing_pinned_stays_quiet(tmp_path, capsys):
    """A project that pins no fields has nothing to report either way."""
    client = MagicMock()
    generate_cmd._report_field_diff(client, None, tmp_path)
    assert not _flat(capsys).strip()


VALUE_SPACE_FIELDS = "fields:\n  - key: pi.nationality\n    type: enum\n    value_space: nationality\n"


def test_a_transport_failure_reading_a_value_space_does_not_crash(tmp_path, monkeypatch, capsys):
    """The branch the widened catch actually broke.

    `_report_field_diff` verifies a value_space-pinned field against the
    registry, and its handler formatted `{e.detail}` — an AethisAPIError-only
    attribute. Widening the catch to httpx therefore turned a dropped
    connection into an AttributeError, which no handler here or at the boundary
    catches: a successful generation ended in a raw traceback.

    Every other test pins members INLINE, so none of them enters this branch —
    which is exactly how the defect reached CI green.
    """
    (tmp_path / "sources").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sources" / "a.md").write_text("the source document")
    (tmp_path / "fields").mkdir(parents=True, exist_ok=True)
    (tmp_path / "fields" / "fields.yaml").write_text(VALUE_SPACE_FIELDS)

    client = _engine(
        {
            "job": {
                "status": "success",
                "result_ruleset_id": "rs_from_this_run",
                "value_spaces_resolved": {"pi.nationality": {"space": "nationality", "version": 3}},
            }
        },
        schema={"fields": [{"field_id": "pi.nationality", "enum_values": ["gbr", "usa"]}]},
    )

    def value_space(name, version=None):
        if version is None:
            return {"name": name, "members": ["gbr", "usa"]}  # the pre-generation probe succeeds
        raise httpx.ConnectError("connection reset")  # the diff-phase read drops

    client.get_value_space.side_effect = value_space
    _wire(monkeypatch, tmp_path, client)

    _run()  # must not raise AttributeError, and must not fail the generation

    out = _flat(capsys)
    assert "NOT verified" in out, "the unverifiable reference must still be reported"
    assert "connection reset" in out, "and the reason must be the transport error, readably"
    assert read_state(tmp_path)["ruleset_id"] == "rs_from_this_run"
