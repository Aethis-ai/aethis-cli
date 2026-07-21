"""Tests for `aethis usage` + X-RateLimit header capture (epic aethis-workspace#552)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import respx
from typer.testing import CliRunner

BASE = "http://localhost:8080"


@respx.mock(base_url=BASE)
def test_client_captures_ratelimit_headers(respx_mock):
    from aethis_cli.client import AethisClient

    respx_mock.get("/api/v1/public/usage").mock(
        return_value=httpx.Response(
            200,
            json={"tier": "free", "classes": [], "rolling": {}},
            headers={
                "X-RateLimit-Class": "read",
                "X-RateLimit-Limit": "100000",
                "X-RateLimit-Remaining": "99997",
                "X-RateLimit-Reset": "1800000000",
            },
        )
    )
    c = AethisClient("ak_test", BASE)
    c.usage()
    assert c.last_rate_limit == {
        "class": "read",
        "limit": 100000,
        "remaining": 99997,
        "reset": 1800000000,
    }


@respx.mock(base_url=BASE)
def test_client_without_ratelimit_headers_leaves_none(respx_mock):
    from aethis_cli.client import AethisClient

    respx_mock.get("/api/v1/public/me").mock(return_value=httpx.Response(200, json={"key_id": "ak"}))
    c = AethisClient("ak_test", BASE)
    c.whoami()
    assert c.last_rate_limit is None


def _run_usage(client_mock, api_key="ak_test"):
    from aethis_cli.main import app

    runner = CliRunner()
    patches = [
        patch("aethis_cli.commands.usage_cmd.AethisClient", return_value=client_mock),
        patch("aethis_cli.commands.usage_cmd.resolve_cached_key", return_value=api_key),
        patch(
            "aethis_cli.commands.usage_cmd.resolve_base_url_with_source",
            return_value=(BASE, "default"),
        ),
    ]
    for p in patches:
        p.start()
    try:
        return runner.invoke(app, ["usage"], catch_exceptions=False)
    finally:
        for p in reversed(patches):
            p.stop()


def test_usage_command_renders_per_class():
    client = MagicMock()
    client.usage.return_value = {
        "tier": "free",
        "classes": [
            {"class": "generate", "used": 3, "limit": 200, "remaining": 197, "reset": 1800000000},
            {"class": "read", "used": 50, "limit": 100000, "remaining": 99950, "reset": 1800000000},
        ],
        "rolling": {"last_7_days": {}, "last_30_days": {}},
    }
    result = _run_usage(client)
    assert result.exit_code == 0
    # Content present whether rendered as the Rich table or the non-TTY JSON.
    assert "generate" in result.output
    assert "197" in result.output


def test_usage_command_requires_key():
    with patch("aethis_cli.commands.usage_cmd.resolve_cached_key", return_value=None):
        from aethis_cli.main import app

        result = CliRunner().invoke(app, ["usage"], catch_exceptions=False)
    assert result.exit_code == 1
