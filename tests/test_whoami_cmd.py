"""Tests for `aethis whoami`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner


def _run(env=None, client_mock=None):
    from aethis_cli.main import app

    runner = CliRunner()
    patches = []
    if client_mock is not None:
        patches.append(patch("aethis_cli.commands.whoami_cmd.AethisClient", return_value=client_mock))
    # Short-circuit the resolve helper so we don't touch keychain in tests
    patches.append(
        patch(
            "aethis_cli.commands.whoami_cmd._resolve_api_key_lax",
            return_value=("ak_test", "http://localhost:8080"),
        ),
    )
    _env = {"AETHIS_BASE_URL": "http://localhost:8080"}
    if env:
        _env.update(env)

    for p in patches:
        p.start()
    try:
        return runner.invoke(app, ["whoami"], env=_env, catch_exceptions=False)
    finally:
        for p in reversed(patches):
            p.stop()


def test_whoami_with_authoring_scope_shows_enabled():
    client = MagicMock()
    client.whoami.return_value = {
        "key_id": "ak_abc123",
        "tenant_id": "tenant_x",
        "scopes": ["decide", "projects:write"],
        "rate_limit_tier": "pro",
        "internal": False,
        "can_author": True,
    }
    result = _run(client_mock=client)
    assert result.exit_code == 0, result.output
    assert "ak_abc123" in result.output
    assert "pro" in result.output
    assert "Authoring enabled" in result.output


def test_whoami_without_authoring_scope_points_to_signup():
    client = MagicMock()
    client.whoami.return_value = {
        "key_id": "ak_abc",
        "tenant_id": "tenant_x",
        "scopes": ["decide"],
        "rate_limit_tier": "free",
        "internal": False,
        "can_author": False,
    }
    result = _run(client_mock=client)
    assert result.exit_code == 0, result.output
    assert "Authoring not available" in result.output
    assert "aethis.ai/developer-access" in result.output


def test_whoami_without_api_key_exits_with_hint():
    # Override the resolver to return (None, base_url)
    from aethis_cli.main import app

    runner = CliRunner()
    with patch(
        "aethis_cli.commands.whoami_cmd._resolve_api_key_lax",
        return_value=(None, "http://localhost:8080"),
    ):
        result = runner.invoke(app, ["whoami"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "No Aethis API key" in result.output
