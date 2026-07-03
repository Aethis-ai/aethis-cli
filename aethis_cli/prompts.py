"""Interactive-prompt helpers with a non-interactive ambient bypass.

A confirmation prompt must never hang a background job, a CI step, or any other
unattended context waiting for stdin. Every destructive command keeps its
explicit ``--yes`` flag; on top of that an ambient env signal
(``AETHIS_NONINTERACTIVE`` or the conventional ``CI``) flips the whole process
non-interactive, so a caller doesn't have to know each command's flag. The
bypass announces itself (fail-loud) so it's never silently active.
"""

from __future__ import annotations

import os
from typing import Optional

import typer

from aethis_cli.output import console

_NONINTERACTIVE_ENVS = ("AETHIS_NONINTERACTIVE", "CI")
_TRUTHY = {"1", "true", "yes"}


def _env_reason() -> Optional[str]:
    """Return the name of the first truthy non-interactive env var, if any."""
    for name in _NONINTERACTIVE_ENVS:
        value = os.environ.get(name)
        if value is not None and value.strip().lower() in _TRUTHY:
            return name
    return None


def is_noninteractive() -> bool:
    """True when an ambient env signal has flipped the process non-interactive."""
    return _env_reason() is not None


def confirm_or_abort(message: str, *, assume_yes: bool = False) -> None:
    """Confirm a destructive action, honouring ``--yes`` and the env bypass.

    Proceeds silently when ``assume_yes`` is set (the explicit ``--yes`` flag).
    Proceeds with a one-line notice when running non-interactively
    (``AETHIS_NONINTERACTIVE`` / ``CI`` truthy), so the bypass is fail-loud, not
    a silent hang. Otherwise it prompts and raises ``typer.Abort`` if the user
    declines.
    """
    if assume_yes:
        return
    reason = _env_reason()
    if reason is not None:
        console.print(f"[dim]Non-interactive ({reason} set): proceeding without prompt: {message}[/dim]")
        return
    if not typer.confirm(message):
        raise typer.Abort()
