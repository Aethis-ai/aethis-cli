"""The CLI prints the engine's two authoring safeguards (aethis-cli#143).

``source_check`` (cited-versus-built check) arrives on publish and promote
responses; ``source_questions`` (conflicting or ambiguous source text authoring
raised) on status, generate-and-test, publish and promote responses. Both are
warn-only and both are optional.

The fixtures under ``tests/fixtures/source_safeguards/`` follow the engine's
response models field for field (``SourceCheck`` and ``SourceQuestion``).

What each group pins down:

* each command prints both fields when the response carries them;
* a response without them — or with an ``ok`` / ``not_run`` check and an empty
  question list — prints exactly what it printed before;
* question text is printed verbatim, never parsed as Rich markup.
"""

from __future__ import annotations

import copy
import json
import re
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from aethis_cli.commands import generate_cmd
from aethis_cli.source_safeguards import render_source_check, render_source_questions

FIXTURES = Path(__file__).parent / "fixtures" / "source_safeguards"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

MISMATCH_CITED = "sha256:303eed8e4cc051601e46f72be2531a428ea29d254df8e9226d150e5a2e820113"
MISMATCH_STAMPED = "sha256:71a97144aa1c0e1f43d1ae1a1a1f7c5b1b2c3d4e5f60718293a4b5c6d7e8f901"

# Strings that only the safeguard renderer prints. Absent fields must produce
# none of them.
NEW_OUTPUT_MARKERS = ("Source check", "source question", "Provisional reading", "mismatch", "unverifiable")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", _ANSI.sub("", text))


def _without_safeguards(payload: dict) -> dict:
    out = copy.deepcopy(payload)
    out.pop("source_check", None)
    out.pop("source_questions", None)
    return out


def _with_silent_safeguards(payload: dict, status: str) -> dict:
    out = copy.deepcopy(payload)
    out["source_check"] = {"status": status, "warnings": []}
    out["source_questions"] = []
    return out


def _assert_no_new_output(out: str) -> None:
    for marker in NEW_OUTPUT_MARKERS:
        assert marker not in out, f"unexpected {marker!r} in output:\n{out}"


def _assert_check_rendered(out: str) -> None:
    assert "Source check: " in out
    assert "mismatch src_act#section-6 (source src_act)" in out
    assert MISMATCH_STAMPED in out
    assert MISMATCH_CITED in out


def _assert_questions_rendered(out: str, count: int) -> None:
    noun = "question" if count == 1 else "questions"
    assert f"{count} source {noun} raised during authoring" in out
    assert "1. conflict [sq_1]" in out
    assert 'src_act#section-6: "a crew member must hold a valid certificate"' in out
    assert 'src_act#section-9: "no certificate is required for training flights"' in out
    assert "Provisional reading: Section 9 excepts training flights from Section 6" in out
    assert "Affects: crit_certificate" in out


# ---------------------------------------------------------------------------
# The renderer
# ---------------------------------------------------------------------------


def test_every_warning_kind_is_described(capsys):
    render_source_check(_fixture("publish_with_safeguards.json")["source_check"])
    out = _flat(capsys.readouterr().out)
    assert "Source check: 3 warning(s)" in out
    _assert_check_rendered(out)
    assert "unverifiable src_legacy#s2 (source src_legacy)" in out
    assert "no authoring inputs recorded" in out


@pytest.mark.parametrize("status", ["ok", "not_run"])
def test_a_clean_or_skipped_check_prints_nothing(capsys, status):
    render_source_check({"status": status, "warnings": []})
    assert capsys.readouterr().out == ""


def test_a_check_that_errored_says_so_without_failing_the_publish(capsys):
    render_source_check({"status": "error", "warnings": []})
    out = _flat(capsys.readouterr().out)
    assert "Source check could not run" in out
    assert "publish itself succeeded" in out


def test_an_unknown_warning_kind_is_shown_not_dropped(capsys):
    render_source_check({"status": "warnings", "warnings": [{"kind": "future_kind", "citation_key": "k1"}]})
    out = _flat(capsys.readouterr().out)
    assert "future_kind" in out
    assert "citation_key=k1" in out


@pytest.mark.parametrize("value", [None, [], "not-a-list", {"id": "x"}])
def test_absent_or_malformed_questions_print_nothing(capsys, value):
    render_source_questions(value)
    assert capsys.readouterr().out == ""


def test_question_inheritance_is_named(capsys):
    render_source_questions(_fixture("publish_with_safeguards.json")["source_questions"])
    out = _flat(capsys.readouterr().out)
    assert "2. ambiguity [sq_2]" in out
    assert "Inherited from rs_seed_7" in out


def test_question_text_is_never_parsed_as_markup(capsys):
    """Quotes and readings come from uploaded sources and model output.

    Mutation: print the quote through ``console.print`` with markup on and
    ``[bold]`` / ``[/]`` vanish (or raise a MarkupError) — this goes red.
    """
    render_source_questions(
        [
            {
                "id": "sq_[red]x[/red]",
                "clauses": [{"citation_key": "k[/]", "quote": "the [bold]applicant[/bold] [/] must [link=x]"}],
                "kind": "conflict",
                "readings": ["a", "b"],
                "provisional_reading": "treat [italic]this[/] as [dim]that",
                "affected_criteria": ["[b]crit[/b]"],
                "inherited_from": "[/red]rs",
            }
        ]
    )
    out = _ANSI.sub("", capsys.readouterr().out)
    assert "[sq_[red]x[/red]]" in out
    assert 'k[/]: "the [bold]applicant[/bold] [/] must [link=x]"' in out
    assert "Provisional reading: treat [italic]this[/] as [dim]that" in out
    assert "Affects: [b]crit[/b]" in out
    assert "Inherited from [/red]rs" in out


def test_warning_text_is_never_parsed_as_markup(capsys):
    render_source_check(
        {
            "status": "warnings",
            "warnings": [{"kind": "unverifiable", "citation_key": "k[bold]1[/]", "source_id": "s[/]"}],
        }
    )
    out = _ANSI.sub("", capsys.readouterr().out)
    assert "k[bold]1[/] (source s[/])" in out


# ---------------------------------------------------------------------------
# aethis publish
# ---------------------------------------------------------------------------


def _publish(publish_response: dict) -> str:
    cfg = SimpleNamespace(base_url="http://engine.test", project_id="proj_test", config_path="/tmp/.aethis")
    client = MagicMock()
    client.run_tests.return_value = {"passed": 1, "total": 1, "failed": 0, "errors": 0, "results": []}
    client.publish.return_value = publish_response
    from aethis_cli.main import app

    with ExitStack() as stack:
        stack.enter_context(patch("aethis_cli.commands.publish_cmd.load_project_config", return_value=cfg))
        stack.enter_context(patch("aethis_cli.commands.publish_cmd.resolve_api_key", return_value="ak"))
        stack.enter_context(patch("aethis_cli.commands.publish_cmd.make_authed_client", return_value=client))
        result = CliRunner().invoke(app, ["publish"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return _flat(result.output)


def test_publish_prints_both_safeguards():
    out = _publish(_fixture("publish_with_safeguards.json"))
    assert "Published ruleset rs_pub_1" in out
    _assert_check_rendered(out)
    _assert_questions_rendered(out, 2)


@pytest.mark.parametrize("status", ["ok", "not_run"])
def test_publish_output_is_unchanged_without_anything_to_report(status):
    payload = _fixture("publish_with_safeguards.json")
    baseline = _publish(_without_safeguards(payload))
    _assert_no_new_output(baseline)
    assert _publish(_with_silent_safeguards(payload, status)) == baseline


# ---------------------------------------------------------------------------
# aethis rulesets promote-to-live
# ---------------------------------------------------------------------------


def _promote(response: dict, tmp_path, monkeypatch) -> str:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    client = MagicMock()
    client.promote_ruleset_to_live.return_value = response
    from aethis_cli.main import app

    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = CliRunner().invoke(
            app,
            ["rulesets", "promote-to-live", "rb_abc", "child_eligibility", "rs_v3"],
            catch_exceptions=False,
            env={},
        )
    assert result.exit_code == 0, result.output
    return _flat(result.output)


def test_promote_prints_both_safeguards(tmp_path, monkeypatch):
    out = _promote(_fixture("promote_with_safeguards.json"), tmp_path, monkeypatch)
    assert "Promoted ruleset" in out
    assert "Source check: 1 warning(s)" in out
    _assert_check_rendered(out)
    _assert_questions_rendered(out, 1)


def test_promote_output_is_unchanged_without_anything_to_report(tmp_path, monkeypatch):
    payload = _fixture("promote_with_safeguards.json")
    baseline = _promote(_without_safeguards(payload), tmp_path, monkeypatch)
    _assert_no_new_output(baseline)
    assert _promote(_with_silent_safeguards(payload, "ok"), tmp_path, monkeypatch) == baseline


# ---------------------------------------------------------------------------
# aethis status
# ---------------------------------------------------------------------------


def _status(status_payload: dict, tmp_path, monkeypatch) -> str:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    me = {"key_id": "ak_x", "tenant_id": "t", "rate_limit_tier": "free", "scopes": [], "can_author": True}
    from aethis_cli.main import app

    with (
        patch("aethis_cli.client.AethisClient.whoami", return_value=me),
        patch("aethis_cli.client.AethisClient.get_status", return_value=status_payload),
    ):
        result = CliRunner().invoke(app, ["status", "-p", "proj_abc"], catch_exceptions=False, env={})
    assert result.exit_code == 0, result.output
    return _flat(result.output)


def test_status_prints_source_questions(tmp_path, monkeypatch):
    out = _status(_fixture("status_with_questions.json"), tmp_path, monkeypatch)
    assert "Ruleset: rs_new" in out
    assert "1 source question raised during authoring" in out
    assert "1. gap [sq_3]" in out
    assert 'src_act#section-2: ""crew" includes passengers"' in out
    assert "Provisional reading: passengers count as crew only for Section 2" in out


def test_status_output_is_unchanged_without_questions(tmp_path, monkeypatch):
    payload = _fixture("status_with_questions.json")
    baseline = _status(_without_safeguards(payload), tmp_path, monkeypatch)
    _assert_no_new_output(baseline)
    empty = copy.deepcopy(payload)
    empty["source_questions"] = []
    assert _status(empty, tmp_path, monkeypatch) == baseline
    nulled = copy.deepcopy(payload)
    nulled["source_questions"] = None
    assert _status(nulled, tmp_path, monkeypatch) == baseline


# ---------------------------------------------------------------------------
# aethis generate --poll
# ---------------------------------------------------------------------------


def _generate(tmp_path, monkeypatch, capsys, *, status_payload: dict, publish_response, no_publish=False) -> str:
    (tmp_path / "sources").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sources" / "a.md").write_text("the source document")
    cfg = SimpleNamespace(config_path=tmp_path, base_url="http://engine.test", project_id="proj_abc", project="p")
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
    client.get_schema.return_value = {"fields": []}
    client.publish.return_value = publish_response
    monkeypatch.setattr(generate_cmd, "load_project_config", lambda: cfg)
    monkeypatch.setattr(generate_cmd, "resolve_api_key", lambda _cfg: "ak")
    monkeypatch.setattr(generate_cmd, "resolve_anthropic_key", lambda _cfg: None)
    monkeypatch.setattr(generate_cmd, "make_authed_client", lambda *_a, **_k: client)
    monkeypatch.setattr(generate_cmd.time, "sleep", lambda *_a, **_k: None)
    capsys.readouterr()
    generate_cmd._run_generate(project_id="proj_abc", poll=True, timeout=30, no_publish=no_publish)
    return _flat(capsys.readouterr().out)


def _status_with(questions) -> dict:
    payload = _fixture("status_with_questions.json")
    if questions is None:
        payload.pop("source_questions")
    else:
        payload["source_questions"] = questions
    return payload


def test_generate_prints_the_publish_check_and_the_questions_once(tmp_path, monkeypatch, capsys):
    published = _fixture("publish_with_safeguards.json")
    out = _generate(tmp_path, monkeypatch, capsys, status_payload=_status_with(None), publish_response=published)
    assert "Done! Ruleset published: rs_new" in out
    _assert_check_rendered(out)
    _assert_questions_rendered(out, 2)
    assert out.count("raised during authoring") == 1


def test_generate_falls_back_to_the_status_questions(tmp_path, monkeypatch, capsys):
    """An engine whose publish omits the questions still gets them printed."""
    published = _without_safeguards(_fixture("publish_with_safeguards.json"))
    status_payload = _status_with(_fixture("status_with_questions.json")["source_questions"])
    out = _generate(tmp_path, monkeypatch, capsys, status_payload=status_payload, publish_response=published)
    assert "1 source question raised during authoring" in out
    assert "1. gap [sq_3]" in out
    assert out.count("raised during authoring") == 1


def test_generate_no_publish_prints_the_status_questions(tmp_path, monkeypatch, capsys):
    status_payload = _fixture("status_with_questions.json")
    out = _generate(
        tmp_path, monkeypatch, capsys, status_payload=status_payload, publish_response=None, no_publish=True
    )
    assert "left unpublished" in out
    assert "1. gap [sq_3]" in out
    assert "Source check" not in out


def test_generate_output_is_unchanged_without_safeguards(tmp_path, monkeypatch, capsys):
    published = _fixture("publish_with_safeguards.json")
    baseline = _generate(
        tmp_path,
        monkeypatch,
        capsys,
        status_payload=_status_with(None),
        publish_response=_without_safeguards(published),
    )
    _assert_no_new_output(baseline)
    silent = _generate(
        tmp_path,
        monkeypatch,
        capsys,
        status_payload=_status_with([]),
        publish_response=_with_silent_safeguards(published, "not_run"),
    )
    assert silent == baseline


# ---------------------------------------------------------------------------
# Terminal control sequences — markup escaping is not enough
# ---------------------------------------------------------------------------

# Each of these reaches a real terminal as a command, not as text: clear the
# screen, a hidden hyperlink (OSC 8), a window-title write (OSC 0), a C1 CSI,
# and a right-to-left override that visually reorders what follows.
HOSTILE = (
    "\x1b[2J",
    "\x1b]8;;http://evil.example\x1b\\click\x1b]8;;\x1b\\",
    "\x1b]0;title\x07",
    "\x9b2J",
    "‮",
    "⁦",
    "‏",
    "\x7f",
)
FAKE_LINE = "\nSource check: 0 warnings"


def _hostile(tag: str) -> str:
    return tag + "".join(HOSTILE) + FAKE_LINE


def _forced_terminal_output(monkeypatch, fn) -> str:
    """Render through a console that believes it is a real terminal."""
    import io

    from rich.console import Console

    from aethis_cli import source_safeguards

    buf = io.StringIO()
    term = Console(file=buf, force_terminal=True, color_system="truecolor", width=400)
    monkeypatch.setattr(source_safeguards, "console", term)
    fn()
    return buf.getvalue()


def _strip_our_own_styling(out: str) -> str:
    # Rich's own SGR colour codes (ESC [ ... m) are ours; strip exactly those
    # so any ESC that remains came from the payload.
    return re.sub(r"\x1b\[[0-9;]*m", "", out)


def _assert_inert(out: str) -> None:
    body = _strip_our_own_styling(out)
    for seq in ("\x1b", "\x9b", "\x07", "\x7f", "‮", "⁦", "‏"):
        assert seq not in body, f"raw {seq!r} reached the terminal:\n{body!r}"
    # The injected newline must not start a line of its own.
    assert not any(line.lstrip().startswith("Source check: 0 warnings") for line in body.splitlines())


def test_question_fields_cannot_drive_the_terminal(monkeypatch):
    questions = [
        {
            "id": _hostile("sq"),
            "clauses": [{"citation_key": _hostile("key"), "quote": _hostile("quote")}],
            "kind": _hostile("conflict"),
            "readings": ["a", "b"],
            "provisional_reading": _hostile("reading"),
            "affected_criteria": [_hostile("crit")],
            "inherited_from": _hostile("rs"),
        }
    ]
    out = _forced_terminal_output(monkeypatch, lambda: render_source_questions(questions))
    _assert_inert(out)
    assert "quote" in out and "reading" in out  # the text itself still prints


def test_warning_fields_cannot_drive_the_terminal(monkeypatch):
    check = {
        "status": "warnings",
        "warnings": [
            {
                "kind": "mismatch",
                "citation_key": _hostile("key"),
                "source_id": _hostile("src"),
                "stamped_digest": _hostile("sha256:aa"),
                "cited_digest": _hostile("sha256:bb"),
            },
            {"kind": _hostile("future"), _hostile("field"): _hostile("value")},
        ],
    }
    out = _forced_terminal_output(monkeypatch, lambda: render_source_check(check))
    _assert_inert(out)
    assert "sha256:aa" in out and "future" in out


def test_sanitiser_escapes_visibly_and_fails_closed():
    from aethis_cli._terminal_safe import safe_text

    assert safe_text("a\x1b[2Jb") == "a\\x1b[2Jb"
    assert safe_text("x‮y") == "x\\u202ey"
    assert safe_text("x\x9by") == "x\\x9by"
    assert safe_text("one\ntwo\r\tthree") == "one two  three"
    assert safe_text(None) == ""
    assert safe_text(42) == "42"
    assert safe_text({"k": "\x1b"}) == "{'k': '\\x1b'}"  # coerced, then sanitised
    assert safe_text("plain [bold] text — £ é 漢") == "plain [bold] text — £ é 漢"


def test_lone_surrogates_are_escaped_not_crashed_on(monkeypatch):
    """A JSON ``\\ud800`` decodes to a lone surrogate, which UTF-8 cannot encode.

    Unescaped, printing it raises UnicodeEncodeError after the publish has
    already succeeded — a traceback that invites a second publish.
    """
    from aethis_cli._terminal_safe import safe_text

    decoded = json.loads('"a\\ud800b\\udfffc"')
    assert safe_text(decoded) == "a\\ud800b\\udfffc"
    questions = [{"id": decoded, "clauses": [{"citation_key": "k", "quote": decoded}], "kind": "gap"}]
    out = _forced_terminal_output(monkeypatch, lambda: render_source_questions(questions))
    out.encode("utf-8")  # raises on a lone surrogate


def test_unicode_line_and_paragraph_separators_are_escaped():
    from aethis_cli._terminal_safe import safe_text

    assert safe_text("a b c") == "a\\u2028b\\u2029c"
