"""An unreachable API must not crash the code that reports it unreachable.

`httpx` exposes `.request` as a **property that raises** `RuntimeError` when
httpx never bound one — so the defensive-looking `getattr(e, "request", None)`
never applies its default, and the transport-error renderer could fail while
reporting a transport failure: a traceback about the error handler in place of
the one actionable line, on the path where something has already gone wrong.

The two pre-existing tests in `test_main.py` both construct the error *with* a
bound `request=`, which is exactly why the defect survived. These cover the
unbound case, and the fallback the renderer is supposed to use for it.
"""

from __future__ import annotations

import re

import httpx

from aethis_cli.output import render_transport_error

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(capsys) -> str:
    return re.sub(r"\s+", " ", _ANSI.sub("", capsys.readouterr().out))


def test_an_error_with_no_bound_request_still_renders(capsys):
    """The shape that raises from the property getter."""
    render_transport_error(httpx.ConnectError("connection refused"))

    out = _flat(capsys)
    assert "Could not reach the Aethis API" in out
    assert "connection refused" in out
    assert "https://api.aethis.ai" in out, "with no request to read a host from, the default is the honest fallback"


def test_the_configured_host_is_named_rather_than_the_public_default(capsys):
    """An author on a private or staging engine must not be told the public host failed."""
    render_transport_error(httpx.ConnectError("connection refused"), base_url="http://engine.internal:8080")

    out = _flat(capsys)
    assert "http://engine.internal:8080" in out
    assert "https://api.aethis.ai" not in out, "naming a host that was never contacted describes the wrong request"


def test_a_bound_request_still_wins(capsys):
    """The pre-existing behaviour the extraction had to preserve."""
    request = httpx.Request("GET", "https://staging.api.aethis.ai/api/v1/public/projects/p/status")
    err = httpx.ReadTimeout("timed out", request=request)

    render_transport_error(err)

    assert "staging.api.aethis.ai" in _flat(capsys)
