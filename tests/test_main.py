from __future__ import annotations

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
