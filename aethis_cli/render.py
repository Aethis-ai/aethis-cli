"""Shared output rendering for list/show commands.

The CLI's list/show commands historically built Rich tables inline and
called ``console.print(table)``. That worked for humans but broke every
scripting workflow — pipes saw ANSI escapes, ``| jq`` was impossible
without scraping, and CI logs were full of box-drawing characters.

This module is the single emit point. Each list/show command:

1. Builds its primary data structure (list of dicts, or a single dict).
2. Either (a) passes that data and a table-rendering callback to
   :func:`emit`, or (b) builds the table inline and passes it as a
   precomputed ``rich.Table`` via ``table=...``.
3. Calls :func:`emit`, which respects ``--output / --json / --jq``
   global flags (set on :data:`RUNTIME` at the root callback).

The flag surface mirrors GitHub's ``gh`` CLI:

* ``--output table|json`` — pick the format. Default: ``table`` on a
  TTY, ``json`` when stdout is piped (gh's "pipe-friendly by default").
* ``--json`` — alias for ``--output json``; with an optional CSV value
  (``--json id,name``) limits the emitted object to those fields. With
  no value (``--json`` alone) prints the available fields and exits 0
  (gh's introspection trick — invaluable for discovering schema).
* ``--jq EXPR`` — pipe JSON output through ``jq`` before printing.
  Requires the ``jq`` binary on PATH; fails with a clear hint if not.

``--jq`` shells out to the ``jq`` binary deliberately: no extra Python
deps, no libjq wheel-build pain on niche platforms, and it matches the
user's existing mental model from ``kubectl --jq``, ``aws --query``,
etc. macOS ships jq via ``brew install jq``; Linux distros via apt /
dnf / pacman.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Optional, Union

import typer
from rich.table import Table

from aethis_cli.output import console


class OutputFormat(str, Enum):
    """Supported output formats. Subclasses ``str`` for free Typer parsing."""

    TABLE = "table"
    JSON = "json"


# Sentinel: ``--json`` passed with no value (request the field list).
LIST_FIELDS_SENTINEL = "__list_fields__"


@dataclass
class RenderOpts:
    """CLI-wide rendering options wired from the root callback.

    Mutable singleton (see :data:`RUNTIME`) so commands and helpers can
    read the user's choices without threading flags through every signature.
    """

    output: Optional[OutputFormat] = None  # None = "use default for the surface"
    json_fields: Optional[str] = None  # None | "" | "field1,field2" | LIST_FIELDS_SENTINEL
    jq_expr: Optional[str] = None

    def reset(self) -> None:
        self.output = None
        self.json_fields = None
        self.jq_expr = None


# Module-level singleton; ``main.cli`` populates it from global Typer flags.
RUNTIME = RenderOpts()


def is_json_requested(opts: Optional[RenderOpts] = None) -> bool:
    """Return True if the active opts ask for JSON output.

    Centralised so commands can short-circuit human-friendly footers
    (``Try: aethis ... ``) when JSON is going to a pipe.
    """
    o = opts or RUNTIME
    if o.output == OutputFormat.JSON:
        return True
    if o.json_fields is not None or o.jq_expr is not None:
        return True
    if o.output is None and not _stdout_is_tty():
        # Pipe-friendly default: non-TTY callers get JSON.
        return True
    return False


def _stdout_is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _resolve_format(opts: RenderOpts) -> OutputFormat:
    """Apply precedence: explicit flag > non-TTY autodetect > table default."""
    if opts.output is not None:
        return opts.output
    if opts.json_fields is not None or opts.jq_expr is not None:
        return OutputFormat.JSON
    if not _stdout_is_tty():
        return OutputFormat.JSON
    return OutputFormat.TABLE


def _available_fields(data: Any) -> list[str]:
    """Best-effort field discovery from the primary resource.

    For a list of dicts, returns the union of keys from the first
    non-empty record (matches gh's behaviour). For a single dict,
    returns its keys. For anything else, returns an empty list.
    """
    if isinstance(data, dict):
        return list(data.keys())
    if isinstance(data, list):
        for record in data:
            if isinstance(record, dict):
                return list(record.keys())
    return []


def _filter_fields(data: Any, fields: list[str]) -> Any:
    """Pluck only the named fields from a dict or list-of-dicts.

    Unknown field names are silently dropped — matches gh. (We surface
    the available field list via the introspection sentinel, so a typo
    is easy to debug.)
    """
    if isinstance(data, dict):
        return {f: data[f] for f in fields if f in data}
    if isinstance(data, list):
        return [{f: r[f] for f in fields if isinstance(r, dict) and f in r} for r in data]
    return data


def _run_jq(json_text: str, expr: str) -> str:
    """Pipe JSON text through ``jq``. Raises typer.Exit on missing binary."""
    if shutil.which("jq") is None:
        console.print(
            "[red]jq binary not found on PATH.[/red]  [dim]Install via "
            "`brew install jq` (macOS), `apt install jq` (Debian/Ubuntu), or "
            "`dnf install jq` (Fedora). Re-run without `--jq` to skip post-processing.[/dim]",
        )
        raise typer.Exit(code=4)

    try:
        result = subprocess.run(
            ["jq", expr],
            input=json_text,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as e:
        console.print(f"[red]Failed to invoke jq:[/red] {e}")
        raise typer.Exit(code=4) from e

    if result.returncode != 0:
        # jq writes the error description to stderr; surface it verbatim
        # so the user can fix their expression.
        msg = result.stderr.strip() or "jq exited with a non-zero status."
        console.print(f"[red]jq error:[/red] {msg}")
        raise typer.Exit(code=2)

    return result.stdout


def _emit_field_list(fields: list[str]) -> None:
    """Handle ``--json`` with no value: print the field list and exit.

    Matches gh's behaviour: introspection is a terminal action — the user
    asked "what can I select?", not "render with everything", so we never
    fall through to the data path. Exits with status 2 to make scripts
    fail loudly if they forgot to specify fields.
    """
    if not fields:
        console.print(
            "[yellow]This command does not declare introspectable fields.[/yellow] "
            "[dim]Try `--output json` to see the raw payload.[/dim]",
        )
        raise typer.Exit(code=2)

    if _stdout_is_tty():
        console.print("[bold]Available fields for --json:[/bold]")
        for f in fields:
            console.print(f"  {f}")
    else:
        # Piped: emit a plain newline-separated list so `xargs` / loops work.
        print("\n".join(fields))
    raise typer.Exit(code=2)


def emit(
    data: Any,
    *,
    table: Optional[Union[Table, Callable[[], Table]]] = None,
    fields: Optional[Iterable[str]] = None,
    opts: Optional[RenderOpts] = None,
) -> None:
    """Render ``data`` according to the active output options.

    Parameters
    ----------
    data
        The primary data structure for the command (list-of-dicts for
        ``list`` commands; dict for ``show`` commands; arbitrary JSON
        for decision endpoints).
    table
        Either a precomputed ``rich.Table`` or a zero-arg callable that
        builds one on demand. Only invoked when format resolves to
        TABLE — saves work when the user piped to JSON. When omitted in
        TABLE mode, falls back to ``console.print_json(data=data)``
        (better than nothing, but commands should provide a table).
    fields
        Explicit override for the available-fields list reported by
        ``--json`` introspection. When omitted, derived from ``data``.
    opts
        Override for the active :data:`RUNTIME` (useful for tests).
    """
    active = opts or RUNTIME

    # ``--json`` with no value: introspection mode, never hits the data.
    if active.json_fields == LIST_FIELDS_SENTINEL:
        _emit_field_list(list(fields) if fields is not None else _available_fields(data))
        return

    fmt = _resolve_format(active)

    if fmt == OutputFormat.TABLE:
        if active.jq_expr is not None:
            # `--jq` implies JSON. If the user asked for both --output table
            # and --jq, that's a contradiction worth flagging.
            console.print(
                "[yellow]--jq requires JSON output; ignoring --output table.[/yellow]",
            )
            fmt = OutputFormat.JSON

    if fmt == OutputFormat.TABLE:
        if table is None:
            # Fall through to pretty-printed JSON — better than crashing
            # when a command forgot to declare its table renderer.
            console.print_json(data=data)
            return
        rendered = table() if callable(table) else table
        console.print(rendered)
        return

    # JSON mode below this point.
    output = data
    if active.json_fields and active.json_fields != LIST_FIELDS_SENTINEL:
        wanted = [f.strip() for f in active.json_fields.split(",") if f.strip()]
        output = _filter_fields(data, wanted)

    text = json.dumps(output, indent=2, default=str, sort_keys=False)

    if active.jq_expr is not None:
        text = _run_jq(text, active.jq_expr)

    # Use ``print`` instead of ``console.print`` so the output is a clean
    # stream of bytes — no Rich markup escaping, no ANSI when piped.
    print(text)


__all__ = [
    "OutputFormat",
    "RenderOpts",
    "RUNTIME",
    "LIST_FIELDS_SENTINEL",
    "emit",
    "is_json_requested",
]
