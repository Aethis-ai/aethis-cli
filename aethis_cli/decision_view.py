"""Human-readable rendering of a decision response.

Three things arrive together in a decision response and a developer needs to
tell them apart at a glance:

* **Result** -- what the caller may act on, plus the immutable identity of the
  rule artefact that produced it (so the same answer can be reproduced later).
* **Logic trace** -- which criteria the engine evaluated and how they came
  out. Explanatory, never a second decision: a criterion may read
  ``satisfied`` inside a response whose result is ``undetermined``, and
  aggregating criterion statuses to re-derive an outcome is exactly the
  mistake the separation is here to prevent.
* **Sources** -- the publish-validated citations behind the criteria: the
  document, its authority and licence, the verbatim quoted text, a
  self-locating deep link, and the digest of the bytes verified at publish
  time.

The renderers live here rather than inside the command modules so ``decide``
and ``explain`` present the same information the same way.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from aethis_cli import contract
from aethis_cli.output import console


def print_blocking_errors(response: Mapping[str, Any]) -> None:
    """Render the blocking-input-error block.

    Printed *instead of* a verdict: an input the ruleset could not apply means
    no terminal result exists, so there is nothing to report but the errors
    and how to fix them.
    """
    errors = contract.blocking_field_errors(response)
    if not errors:
        return

    console.print("\n[bold]Result[/bold]")
    console.print("  [red]blocked[/red] -- one or more inputs were rejected, so there is no decision.")
    console.print("  [dim]No result is defined when the input set was rejected.[/dim]")

    console.print("\n[bold]Rejected inputs[/bold]")
    for field_id, message in errors.items():
        console.print(f"  [red]x[/red] {field_id}", highlight=False)
        console.print(f"    {message}", style="dim", markup=False, highlight=False)

    console.print("\n[dim]Check each field id and value type against `aethis fields -b <ruleset>`, then re-run.[/dim]")

    for violation in contract.contract_violations(response):
        console.print(f"[yellow]![/yellow] Contract violation: {violation}", highlight=False)


def print_identity(response: Mapping[str, Any], *, heading: bool = True) -> None:
    """Render the immutable identity of the artefact behind a response."""
    ident = contract.resolved_identity(response)
    if heading:
        console.print("\n[bold]Ruleset identity[/bold]")

    if ident.slug:
        console.print(f"  Slug:      {ident.slug}", highlight=False)
    if ident.ruleset_id:
        console.print(f"  Ruleset:   {ident.ruleset_id}", highlight=False)
    if ident.rulebook_id:
        console.print(f"  Rulebook:  {ident.rulebook_id}", highlight=False)
    if ident.ruleset_version:
        style = "red" if ident.ruleset_version == contract.UNRESOLVED_VERSION else ""
        rendered = f"[{style}]{ident.ruleset_version}[/{style}]" if style else ident.ruleset_version
        console.print(f"  Version:   {rendered}", highlight=False)
    if ident.content_digest:
        console.print(f"  Digest:    {ident.content_digest}", highlight=False)
    if ident.engine_version:
        console.print(f"  Engine:    {ident.engine_version}", highlight=False)
    if ident.decision_id:
        console.print(f"  Decision id: {ident.decision_id}", highlight=False)
    if ident.inputs_hash:
        console.print(f"  Inputs hash: {ident.inputs_hash}", highlight=False)

    if ident.unresolved:
        console.print(
            "  [yellow]![/yellow] Unresolved: "
            + ", ".join(ident.unresolved)
            + " -- this result cannot be reproduced from the response alone.",
            highlight=False,
        )


def _print_reference(reference: Mapping[str, Any], indent: str = "      ") -> None:
    title = reference.get("title") or reference.get("source_id") or "(untitled source)"
    authority = reference.get("authority")
    header = f"{indent}[cyan]{title}[/cyan]"
    if authority:
        header += f" [dim]-- {authority}[/dim]"
    console.print(header, highlight=False)

    locator = reference.get("locator")
    if locator:
        console.print(f"{indent}  Locator:  {locator}", style="dim", markup=False, highlight=False)

    quote = contract.quoted_text(reference)
    if quote:
        console.print(f'{indent}  Quote:    "{quote}"', markup=False, highlight=False)

    link = reference.get("deep_link") or reference.get("url")
    if link:
        console.print(f"{indent}  Link:     {link}", style="dim", markup=False, highlight=False)

    version = reference.get("source_version") or reference.get("source_date")
    if version:
        console.print(f"{indent}  Source:   {version}", style="dim", markup=False, highlight=False)

    licence = reference.get("licence")
    verified = reference.get("verified_at")
    if licence or verified:
        bits = []
        if licence:
            bits.append(f"licence {licence}")
        if verified:
            bits.append(f"verified {verified}")
        console.print(f"{indent}  {', '.join(bits)}", style="dim", markup=False, highlight=False)

    digest = reference.get("content_digest")
    if digest:
        console.print(f"{indent}  Digest:   {digest}", style="dim", markup=False, highlight=False)

    gaps = contract.source_reference_gaps(reference)
    if gaps:
        console.print(
            f"{indent}  [yellow]![/yellow] Incomplete reference -- missing: {', '.join(gaps)}",
            highlight=False,
        )


def print_sources(
    cited: Iterable[contract.CitedCriterion],
    *,
    empty_note: Optional[str] = None,
) -> None:
    """Render the supporting-source block.

    ``empty_note`` is printed when nothing was cited -- silence would read as
    "no sources needed" rather than "this ruleset publishes none", and the
    difference matters to anyone auditing a decision.
    """
    cited = list(cited)
    console.print("\n[bold]Sources[/bold]")
    if not cited:
        console.print(
            f"  [dim]{empty_note or 'No published source references for this ruleset.'}[/dim]",
            highlight=False,
        )
        return

    for entry in cited:
        label = entry.title or entry.criterion_id
        suffix = f" [dim]({entry.criterion_id})[/dim]" if entry.criterion_id and entry.criterion_id != label else ""
        console.print(f"\n  {label}{suffix}", highlight=False)
        for reference in entry.references:
            _print_reference(reference)


__all__ = ["print_blocking_errors", "print_identity", "print_sources"]
