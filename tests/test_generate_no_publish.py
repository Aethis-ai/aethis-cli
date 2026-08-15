"""`aethis generate --no-publish` generates without activating anything.

`aethis generate --poll` publishes on success, and publishing *activates* the
ruleset. For a class of authoring work that is the wrong default and can be a
policy breach in itself: a ruleset that must stay a draft on the engine it was
authored against, and only ever activate somewhere else after promotion. The
workaround was to archive it immediately afterwards, which leaves a real window
where it is live.

The flag is deliberately narrow. It suppresses the publish call and says so;
everything else about the run is untouched — the poll, its timeout, and the
post-generation field diff all behave exactly as they do without it, because
those are what make the run worth doing at all. And absent the flag nothing
changes: the publish still happens on the same call with the same argument.

What each test pins down, and what breaks it, is stated per test — an assertion
nobody has watched fail is not evidence that it discriminates.
"""

from __future__ import annotations

import inspect
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer

from aethis_cli.commands import generate_cmd

# One pinned field the stand-in engine will not produce, so the drift report
# has something to say. A diff that finds nothing prints a clean line either
# way, which would not distinguish "the diff ran" from "the diff was skipped".
PINNED_FIELDS = "fields:\n  - key: applicant.date_of_birth\n    type: date\n"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(capsys) -> str:
    """Content, not presentation — rich wraps and colourises captured output."""
    return re.sub(r"\s+", " ", _ANSI.sub("", capsys.readouterr().out))


def _project(tmp_path):
    (tmp_path / "sources").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sources" / "a.md").write_text("the source document")
    (tmp_path / "fields").mkdir(parents=True, exist_ok=True)
    (tmp_path / "fields" / "fields.yaml").write_text(PINNED_FIELDS)
    return tmp_path


def _wire(monkeypatch, tmp_path, client) -> None:
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


def _engine(status_payload: dict, schema: dict | None = None) -> MagicMock:
    client = MagicMock()
    client.generate.return_value = {"job_id": "job_1"}
    client.last_rate_limit = None
    client.get_status.return_value = status_payload
    client.get_schema.return_value = schema or {"fields": []}
    return client


SUCCESS = {"job": {"status": "success", "progress_percent": 100}, "latest_ruleset_id": "rs_new"}


# ---------------------------------------------------------------------------
# The publish call itself — the producer these tests exist to mutate
# ---------------------------------------------------------------------------


def test_no_publish_never_publishes(tmp_path, monkeypatch):
    """The whole point: a successful generation that activates nothing.

    Mutation: leave `client.publish(pid)` in place on the flagged path and this
    goes red. It is the only assertion here that does, which is why it is the
    one the acceptance names.
    """
    _project(tmp_path)
    client = _engine(SUCCESS)
    _wire(monkeypatch, tmp_path, client)

    generate_cmd._run_generate(project_id="proj_abc", poll=True, timeout=30, no_publish=True)

    client.publish.assert_not_called()


def test_without_the_flag_the_publish_is_unchanged(tmp_path, monkeypatch):
    """Absent the flag, the same call with the same argument as before.

    This is the half that catches the *growth* direction: a later branch that
    reaches publish by some other route, or one that stops reaching it at all,
    both show up here rather than in the flagged test.
    """
    _project(tmp_path)
    client = _engine(SUCCESS)
    _wire(monkeypatch, tmp_path, client)

    generate_cmd._run_generate(project_id="proj_abc", poll=True, timeout=30)

    client.publish.assert_called_once_with("proj_abc")


def test_the_flag_defaults_off_for_every_caller(tmp_path, monkeypatch):
    """`aethis refine` shares this machinery and was not asked to change.

    `_run_generate` is called by refine_cmd without the keyword, so a default
    of True — or a required parameter — would silently change a command this
    work never touched.
    """
    _project(tmp_path)
    client = _engine(SUCCESS)
    _wire(monkeypatch, tmp_path, client)

    generate_cmd._run_generate(project_id="proj_abc", poll=True, timeout=30, mode="refine")

    client.publish.assert_called_once_with("proj_abc")


# ---------------------------------------------------------------------------
# Saying so — an unpublished ruleset the author does not know about is worse
# ---------------------------------------------------------------------------


def test_no_publish_says_the_ruleset_was_left_unpublished(tmp_path, monkeypatch, capsys):
    """Name the artefact, say it is not live, and say how to make it live."""
    _project(tmp_path)
    client = _engine(SUCCESS)
    _wire(monkeypatch, tmp_path, client)

    generate_cmd._run_generate(project_id="proj_abc", poll=True, timeout=30, no_publish=True)

    out = _flat(capsys)
    assert "rs_new" in out
    assert "unpublished" in out
    assert "aethis publish" in out


def test_no_publish_says_so_even_when_no_ruleset_id_surfaces(tmp_path, monkeypatch, capsys):
    """The engine can report success before either id lands; still say it.

    The pre-existing success message for this case does not mention publishing
    at all, so inheriting it would leave the one run whose state is hardest to
    guess as the one the CLI says least about.
    """
    _project(tmp_path)
    client = _engine({"job": {"status": "success", "progress_percent": 100}})
    _wire(monkeypatch, tmp_path, client)

    generate_cmd._run_generate(project_id="proj_abc", poll=True, timeout=30, no_publish=True)

    out = _flat(capsys)
    assert "unpublished" in out
    assert "aethis publish" in out


def test_the_deliberate_message_is_not_the_publish_failure_message(tmp_path, monkeypatch, capsys):
    """A choice and a failed publish are different endings and must read so.

    Both leave a draft and both point at `aethis publish`. Collapsing them
    would tell an author who did not pass the flag that everything went to
    plan, which is the case where they most need to know it did not.
    """
    _project(tmp_path)
    client = _engine(SUCCESS)
    client.publish.side_effect = generate_cmd.AethisAPIError(409, "publish refused")
    _wire(monkeypatch, tmp_path, client)

    generate_cmd._run_generate(project_id="proj_abc", poll=True, timeout=30)

    assert "unpublished" not in _flat(capsys)


# ---------------------------------------------------------------------------
# Everything else about the run is untouched
# ---------------------------------------------------------------------------


def test_no_publish_still_reports_the_field_diff(tmp_path, monkeypatch, capsys):
    """The diff is the reason to poll at all; suppressing publish must not cost it."""
    _project(tmp_path)
    client = _engine(SUCCESS, schema={"fields": [{"field_id": "applicant.surprise"}]})
    _wire(monkeypatch, tmp_path, client)

    generate_cmd._run_generate(project_id="proj_abc", poll=True, timeout=30, no_publish=True)

    out = _flat(capsys)
    assert "applicant.date_of_birth" in out  # pinned, not produced
    assert "applicant.surprise" in out  # produced, not pinned


def test_no_publish_still_honours_the_polling_timeout(tmp_path, monkeypatch, capsys):
    """A job that never finishes still ends the run, and still publishes nothing."""
    _project(tmp_path)
    client = _engine({"job": {"status": "running", "progress_percent": 10}})
    _wire(monkeypatch, tmp_path, client)

    with pytest.raises(typer.Exit):
        generate_cmd._run_generate(project_id="proj_abc", poll=True, timeout=0, no_publish=True)

    assert "Timed out" in _flat(capsys)
    client.publish.assert_not_called()


def test_no_publish_is_reachable_from_the_command_surface():
    """The flag exists on `aethis generate` and is off by default.

    On its own this proves only that an option parses, which is why it is the
    least of these tests — the behaviour is pinned above. It is here because
    wiring the plumbing and forgetting the option is a real way to ship a flag
    that every behavioural test passes without.
    """
    option = inspect.signature(generate_cmd.generate).parameters["no_publish"].default
    assert "--no-publish" in option.param_decls
    assert option.default is False
