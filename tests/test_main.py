from __future__ import annotations

from unittest.mock import patch

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
