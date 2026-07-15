"""Regression tests for readable API-error rendering (aethis_cli.output)."""

from __future__ import annotations

import re

from aethis_cli import output
from aethis_cli.output import format_error_detail, render_api_error

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Strip ANSI styling so assertions see the literal rendered characters."""
    return _ANSI.sub("", text)


def test_format_error_detail_coerces_non_string_missing_permissions() -> None:
    """A non-string item in missing_permissions must not raise (ships to users)."""
    detail = {
        "reason_code": "denied_missing_permission",
        "action": "scope.projects:read",
        "missing_permissions": [123, "projects:read"],
        "message": "denied",
    }
    line = format_error_detail(detail)
    assert "123" in line
    assert "projects:read" in line


def test_render_api_error_preserves_bracketed_hint() -> None:
    """A hint containing [brackets] must render verbatim, not be eaten as markup."""
    detail = {
        "reason_code": "denied_missing_permission",
        "missing_permissions": ["projects:read"],
        "message": "denied",
        "hint": "Request access [beta] at https://aethis.ai/sign-up",
    }
    with output.console.capture() as capture:
        render_api_error(403, detail)
    rendered = _plain(capture.get())
    assert "[beta]" in rendered
    assert "https://aethis.ai/sign-up" in rendered


def test_render_api_error_string_detail_passes_through() -> None:
    with output.console.capture() as capture:
        render_api_error(400, "Invalid scope(s): foo")
    assert "Invalid scope(s): foo" in _plain(capture.get())
