"""Tests for the shared output renderer."""

from __future__ import annotations

import json
import shutil
from unittest.mock import patch

import pytest
import typer
from rich.table import Table

from aethis_cli import render
from aethis_cli.render import LIST_FIELDS_SENTINEL, OutputFormat, RenderOpts, emit


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    """Make sure no test leaks RenderOpts state into the next one."""
    render.RUNTIME.reset()
    yield
    render.RUNTIME.reset()


SAMPLE_LIST = [
    {"slug": "aethis/uk-fsm", "ruleset_id": "rs_1", "name": "FSM child elig", "rules": 12},
    {"slug": "aethis/gdpr-toy", "ruleset_id": "rs_2", "name": "GDPR toy", "rules": 5},
]

SAMPLE_DICT = {"key_id": "ak_abc", "tenant_id": "t_1", "tier": "internal"}


def _make_table() -> Table:
    t = Table()
    t.add_column("Slug")
    for row in SAMPLE_LIST:
        t.add_row(row["slug"])
    return t


def test_emit_table_mode_calls_table_callback(capsys: pytest.CaptureFixture[str]) -> None:
    opts = RenderOpts(output=OutputFormat.TABLE)
    emit(SAMPLE_LIST, table=_make_table, opts=opts)
    out = capsys.readouterr().out
    # Rich box-drawing characters confirm a table was rendered
    assert "Slug" in out
    assert "aethis/uk-fsm" in out


def test_emit_json_mode_emits_full_payload(capsys: pytest.CaptureFixture[str]) -> None:
    opts = RenderOpts(output=OutputFormat.JSON)
    emit(SAMPLE_LIST, table=_make_table, opts=opts)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == SAMPLE_LIST


def test_emit_json_filters_fields(capsys: pytest.CaptureFixture[str]) -> None:
    opts = RenderOpts(output=OutputFormat.JSON, json_fields="slug,rules")
    emit(SAMPLE_LIST, opts=opts)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == [
        {"slug": "aethis/uk-fsm", "rules": 12},
        {"slug": "aethis/gdpr-toy", "rules": 5},
    ]


def test_emit_json_filters_unknown_fields_silently(capsys: pytest.CaptureFixture[str]) -> None:
    """Unknown field names get dropped (matches gh). The introspection
    sentinel is the way to discover what's available."""
    opts = RenderOpts(output=OutputFormat.JSON, json_fields="slug,not_a_real_field")
    emit(SAMPLE_LIST, opts=opts)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == [{"slug": "aethis/uk-fsm"}, {"slug": "aethis/gdpr-toy"}]


def test_emit_json_filter_dict(capsys: pytest.CaptureFixture[str]) -> None:
    opts = RenderOpts(output=OutputFormat.JSON, json_fields="key_id,tier")
    emit(SAMPLE_DICT, opts=opts)
    out = capsys.readouterr().out
    assert json.loads(out) == {"key_id": "ak_abc", "tier": "internal"}


def test_emit_field_introspection_lists_keys(capsys: pytest.CaptureFixture[str]) -> None:
    opts = RenderOpts(json_fields=LIST_FIELDS_SENTINEL)
    # Patch isatty so the TTY-branch (Rich-formatted list) fires
    with patch("aethis_cli.render._stdout_is_tty", return_value=True):
        with pytest.raises(typer.Exit):
            emit(SAMPLE_LIST, opts=opts)
    out = capsys.readouterr().out
    for field_name in ("slug", "ruleset_id", "name", "rules"):
        assert field_name in out


def test_emit_field_introspection_piped_emits_newline_list(capsys: pytest.CaptureFixture[str]) -> None:
    opts = RenderOpts(json_fields=LIST_FIELDS_SENTINEL)
    with patch("aethis_cli.render._stdout_is_tty", return_value=False):
        with pytest.raises(typer.Exit):
            emit(SAMPLE_LIST, opts=opts)
    out = capsys.readouterr().out.strip()
    assert out.splitlines() == ["slug", "ruleset_id", "name", "rules"]


def test_emit_field_introspection_empty_data_warns(capsys: pytest.CaptureFixture[str]) -> None:
    opts = RenderOpts(json_fields=LIST_FIELDS_SENTINEL)
    with pytest.raises(typer.Exit) as exc_info:
        emit([], opts=opts)
    assert exc_info.value.exit_code == 2
    assert "does not declare introspectable fields" in capsys.readouterr().out


def test_emit_field_introspection_explicit_fields_override(capsys: pytest.CaptureFixture[str]) -> None:
    """When the caller passes ``fields=[...]``, that wins over data-derived keys.

    Needed for ``decide`` etc. where the payload is nested and the
    user-facing field list is curated.
    """
    opts = RenderOpts(json_fields=LIST_FIELDS_SENTINEL)
    with patch("aethis_cli.render._stdout_is_tty", return_value=False):
        with pytest.raises(typer.Exit):
            emit({"raw": "nested"}, fields=["decision", "reason"], opts=opts)
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["decision", "reason"]


def test_non_tty_defaults_to_json(capsys: pytest.CaptureFixture[str]) -> None:
    """Pipe-friendly default: when stdout isn't a TTY, emit JSON."""
    opts = RenderOpts()  # output=None, no flags
    with patch("aethis_cli.render._stdout_is_tty", return_value=False):
        emit(SAMPLE_LIST, table=_make_table, opts=opts)
    out = capsys.readouterr().out
    assert json.loads(out) == SAMPLE_LIST


def test_tty_defaults_to_table(capsys: pytest.CaptureFixture[str]) -> None:
    opts = RenderOpts()
    with patch("aethis_cli.render._stdout_is_tty", return_value=True):
        emit(SAMPLE_LIST, table=_make_table, opts=opts)
    out = capsys.readouterr().out
    assert "aethis/uk-fsm" in out
    # No JSON braces — that would mean we leaked the JSON branch
    assert "{" not in out


def test_jq_pipes_through_binary(capsys: pytest.CaptureFixture[str]) -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq binary not installed on this machine")
    opts = RenderOpts(jq_expr=".[0].slug")
    emit(SAMPLE_LIST, opts=opts)
    out = capsys.readouterr().out.strip()
    assert out == '"aethis/uk-fsm"'


def test_jq_missing_binary_exits_with_hint(capsys: pytest.CaptureFixture[str]) -> None:
    opts = RenderOpts(jq_expr=".[0]")
    with patch("aethis_cli.render.shutil.which", return_value=None):
        with pytest.raises(typer.Exit) as exc_info:
            emit(SAMPLE_LIST, opts=opts)
    assert exc_info.value.exit_code == 4
    out = capsys.readouterr().out
    assert "jq binary not found" in out
    assert "brew install jq" in out


def test_jq_with_invalid_expression_surfaces_jq_error(capsys: pytest.CaptureFixture[str]) -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq binary not installed on this machine")
    opts = RenderOpts(jq_expr="this is not valid jq")
    with pytest.raises(typer.Exit) as exc_info:
        emit(SAMPLE_LIST, opts=opts)
    assert exc_info.value.exit_code == 2
    assert "jq error:" in capsys.readouterr().out


def test_jq_implies_json_even_with_output_table(capsys: pytest.CaptureFixture[str]) -> None:
    """--jq with --output table is a contradiction; we honour --jq and warn."""
    if shutil.which("jq") is None:
        pytest.skip("jq binary not installed on this machine")
    opts = RenderOpts(output=OutputFormat.TABLE, jq_expr=".[0].slug")
    emit(SAMPLE_LIST, table=_make_table, opts=opts)
    out = capsys.readouterr().out
    assert "--jq requires JSON" in out
    assert '"aethis/uk-fsm"' in out


def test_is_json_requested_table_default_with_tty() -> None:
    with patch("aethis_cli.render._stdout_is_tty", return_value=True):
        assert render.is_json_requested(RenderOpts()) is False


def test_is_json_requested_non_tty() -> None:
    with patch("aethis_cli.render._stdout_is_tty", return_value=False):
        assert render.is_json_requested(RenderOpts()) is True


def test_is_json_requested_explicit_json() -> None:
    assert render.is_json_requested(RenderOpts(output=OutputFormat.JSON)) is True


def test_is_json_requested_jq_implies_json() -> None:
    with patch("aethis_cli.render._stdout_is_tty", return_value=True):
        assert render.is_json_requested(RenderOpts(jq_expr=".")) is True


def test_emit_falls_back_to_print_json_when_no_table(capsys: pytest.CaptureFixture[str]) -> None:
    """Forgetting to pass ``table`` shouldn't crash — emit pretty JSON."""
    opts = RenderOpts(output=OutputFormat.TABLE)
    emit(SAMPLE_DICT, opts=opts)
    out = capsys.readouterr().out
    assert "ak_abc" in out
