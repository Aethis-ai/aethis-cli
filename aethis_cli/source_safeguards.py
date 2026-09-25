"""Render the two authoring safeguards an engine response can carry.

``source_check`` — the cited-versus-built check. On publish and promote the
engine compares the bytes each citation resolves to with the bytes the ruleset
was generated from, and reports ``{status, warnings}``. It is warn-only: the
publish has already succeeded when it is printed.

``source_questions`` — conflicting, ambiguous or missing source text that
authoring raised instead of resolving silently. Each carries the quoted
clauses and the provisional reading the ruleset was built on.

Both are optional on every response: an engine that predates them, or a
response with nothing to report, prints nothing new here.

Everything printed from these fields is text the engine derived from uploaded
sources or model output. Every such line goes through ``_plain``, the one
print boundary here, which (1) sanitises terminal control characters with
``safe_text`` — escape sequences, C1 controls, bidirectional overrides and
embedded newlines — and (2) prints the result as a ``Text``, so Rich markup
such as ``[bold]`` or ``[/]`` is shown rather than interpreted.
"""

from __future__ import annotations

from typing import Any, Optional

from rich.text import Text

from aethis_cli._terminal_safe import safe_text
from aethis_cli.output import console

# Statuses that have something to say. ``ok`` and ``not_run`` are silent,
# matching the rest of the CLI, which prints warnings rather than all-clears.
_REPORTED_CHECK_STATUSES = ("warnings", "error")


def _plain(text: str, *, style: str = "") -> None:
    """Print untrusted text: control characters escaped, no markup, no highlighting."""
    console.print(Text(safe_text(text), style=style), highlight=False)


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _describe_warning(warning: Any) -> list[str]:
    """One warning as display lines (first line is the headline)."""
    if not isinstance(warning, dict):
        return [_str(warning)]
    kind = _str(warning.get("kind")) or "unknown"
    key = _str(warning.get("citation_key"))
    source_id = _str(warning.get("source_id"))
    where = f"{key} (source {source_id})" if key and source_id else (key or source_id)
    if kind == "mismatch":
        return [
            f"mismatch  {where}: the cited document is not the one the ruleset was generated from",
            f"    generated from {_str(warning.get('stamped_digest'))}",
            f"    cited          {_str(warning.get('cited_digest'))}",
        ]
    if kind == "unverifiable":
        return [f"unverifiable  {where}: the digests could not be compared"]
    if kind == "no_authoring_inputs_recorded":
        return [
            "no authoring inputs recorded: this ruleset records no source digests, "
            "so its citations could not be checked against what it was generated from"
        ]
    # A kind this CLI does not know yet: show what the engine sent rather
    # than dropping it.
    extra = ", ".join(f"{k}={_str(v)}" for k, v in warning.items() if k != "kind")
    return [f"{kind}  {extra}" if extra else kind]


def render_source_check(check: Any) -> None:
    """Print a ``source_check`` when it has warnings or failed to run."""
    if not isinstance(check, dict):
        return
    status = check.get("status")
    if status not in _REPORTED_CHECK_STATUSES:
        return
    warnings = check.get("warnings")
    warnings = warnings if isinstance(warnings, list) else []
    console.print()
    if status == "error":
        console.print(
            "[yellow]![/yellow] Source check could not run. The publish itself succeeded; "
            "the cited sources were not compared with the generation sources."
        )
    else:
        console.print(f"[yellow]![/yellow] Source check: {len(warnings)} warning(s)")
    for warning in warnings:
        lines = _describe_warning(warning)
        _plain(f"  {lines[0]}", style="yellow")
        for line in lines[1:]:
            _plain(f"  {line}", style="dim")


def _render_question(index: int, question: Any) -> None:
    if not isinstance(question, dict):
        _plain(f"  {index}. {_str(question)}")
        return
    kind = _str(question.get("kind")) or "question"
    qid = _str(question.get("id"))
    _plain(f"  {index}. {kind}" + (f"  [{qid}]" if qid else ""), style="bold")
    clauses = question.get("clauses")
    for clause in clauses if isinstance(clauses, list) else []:
        if isinstance(clause, dict):
            key = _str(clause.get("citation_key"))
            quote = _str(clause.get("quote"))
            _plain(f'     {key}: "{quote}"' if key else f'     "{quote}"')
        else:
            _plain(f"     {_str(clause)}")
    provisional = _str(question.get("provisional_reading"))
    if provisional:
        _plain(f"     Provisional reading: {provisional}")
    affected = question.get("affected_criteria")
    if isinstance(affected, list) and affected:
        _plain("     Affects: " + ", ".join(_str(a) for a in affected), style="dim")
    inherited = _str(question.get("inherited_from"))
    if inherited:
        _plain(f"     Inherited from {inherited}", style="dim")


def render_source_questions(questions: Any) -> None:
    """Print ``source_questions`` when present and non-empty."""
    if not isinstance(questions, list) or not questions:
        return
    console.print()
    noun = "question" if len(questions) == 1 else "questions"
    console.print(
        f"[yellow]![/yellow] {len(questions)} source {noun} raised during authoring. "
        "The ruleset uses a provisional reading for each:"
    )
    for index, question in enumerate(questions, start=1):
        _render_question(index, question)
    console.print("  [dim]Resolve them with guidance and a fresh regeneration.[/dim]")


def render_source_safeguards(payload: Any, *, questions_from: Optional[Any] = None) -> None:
    """Print both safeguards from one engine response.

    ``questions_from`` overrides where ``source_questions`` are read from, for
    a command that holds two responses and must print the questions once.
    """
    if isinstance(payload, dict):
        render_source_check(payload.get("source_check"))
    source = payload if questions_from is None else questions_from
    if isinstance(source, dict):
        render_source_questions(source.get("source_questions"))
