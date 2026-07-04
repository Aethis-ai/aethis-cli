from __future__ import annotations

from unittest.mock import patch

import httpx

from aethis_cli.main import _format_error_detail


def test_format_error_detail_for_authz_payload() -> None:
    detail = _format_error_detail(
        {
            "reason_code": "denied_missing_permission",
            "action": "project.write",
            "missing_permissions": ["projects:write"],
            "message": "Forbidden",
        }
    )
    assert "reason=denied_missing_permission" in detail
    assert "action=project.write" in detail
    assert "missing=projects:write" in detail


def test_config_error_from_cli_wrapper_is_one_line(monkeypatch, capsys) -> None:
    """cli() entrypoint renders ConfigError as a one-line message, not a traceback."""
    from aethis_cli.errors import ConfigError
    from aethis_cli.main import cli

    with patch("aethis_cli.main.app", side_effect=ConfigError("No aethis.yaml found")):
        try:
            cli()
        except SystemExit as e:
            assert e.code == 1

    captured = capsys.readouterr()
    assert "No aethis.yaml found" in captured.out
    assert "Traceback" not in captured.out
    assert "╭─" not in captured.out


def test_network_error_from_cli_wrapper_is_one_line(capsys) -> None:
    """cli() renders an unreachable API as a one-line message with the URL, not a traceback."""
    from aethis_cli.main import cli

    request = httpx.Request("POST", "https://api.aethis.ai/api/v1/public/decide")
    err = httpx.ConnectError("[Errno 61] Connection refused", request=request)

    with patch("aethis_cli.update_check.start_background_check"), patch("aethis_cli.main.app", side_effect=err):
        try:
            cli()
        except SystemExit as e:
            assert e.code == 1
        else:  # pragma: no cover - the handler must exit non-zero
            raise AssertionError("cli() should have raised SystemExit(1)")

    captured = capsys.readouterr()
    assert "Could not reach the Aethis API" in captured.out
    assert "api.aethis.ai" in captured.out
    assert "Connection refused" in captured.out
    assert "Traceback" not in captured.out


def test_timeout_error_from_cli_wrapper_is_one_line(capsys) -> None:
    """A slow/timing-out API is handled by the same umbrella httpx.HTTPError branch."""
    from aethis_cli.main import cli

    request = httpx.Request("GET", "https://api.aethis.ai/api/v1/public/me")
    err = httpx.ReadTimeout("timed out", request=request)

    with patch("aethis_cli.update_check.start_background_check"), patch("aethis_cli.main.app", side_effect=err):
        try:
            cli()
        except SystemExit as e:
            assert e.code == 1
        else:  # pragma: no cover
            raise AssertionError("cli() should have raised SystemExit(1)")

    captured = capsys.readouterr()
    assert "Could not reach the Aethis API" in captured.out
    assert "Traceback" not in captured.out
