"""Tests for the auth-provider registry."""

from __future__ import annotations

import pytest

from aethis_cli.auth_providers import (
    ProviderContext,
    UnknownAuthMode,
    get_provider,
    known_modes,
    register_provider,
)


def _ctx(api_key: str | None = None, audience: str | None = None) -> ProviderContext:
    profile: dict = {}
    if audience:
        profile["audience"] = audience
    return ProviderContext(api_key=api_key, base_url="https://api.aethis.ai", profile=profile)


def test_api_key_provider_emits_x_api_key_header() -> None:
    headers = get_provider("api_key")(_ctx(api_key="ak_live_abc"))
    assert headers == {"X-API-Key": "ak_live_abc"}


def test_api_key_provider_returns_empty_when_key_missing() -> None:
    # Anonymous decision endpoints still go through this provider when no
    # cached key is present — the absence is expected, not an error.
    headers = get_provider("api_key")(_ctx(api_key=None))
    assert headers == {}


def test_none_provider_always_empty() -> None:
    assert get_provider("none")(_ctx(api_key="ak_live_abc")) == {}
    assert get_provider("none")(_ctx(api_key=None)) == {}


def test_unknown_mode_raises_with_helpful_message() -> None:
    with pytest.raises(UnknownAuthMode) as exc_info:
        get_provider("gcloud_id_token")
    msg = str(exc_info.value)
    assert "gcloud_id_token" in msg
    assert "api_key" in msg  # lists registered modes
    assert "aethis-cli-internal" in msg


def test_register_and_lookup_plugin_provider() -> None:
    def stub_provider(ctx: ProviderContext) -> dict[str, str]:
        return {"Authorization": f"Bearer fake-token-for-{ctx.profile.get('audience')}"}

    register_provider("test_provider", stub_provider)
    try:
        assert "test_provider" in known_modes()
        headers = get_provider("test_provider")(_ctx(audience="aud-1"))
        assert headers == {"Authorization": "Bearer fake-token-for-aud-1"}
    finally:
        # Don't leak the test provider into other test modules.
        from aethis_cli import auth_providers

        auth_providers._PROVIDERS.pop("test_provider", None)


def test_re_register_replaces_provider() -> None:
    from aethis_cli import auth_providers

    original = auth_providers._PROVIDERS["api_key"]
    try:
        register_provider("api_key", lambda ctx: {"X-Other": "x"})
        assert get_provider("api_key")(_ctx()) == {"X-Other": "x"}
    finally:
        auth_providers._PROVIDERS["api_key"] = original
