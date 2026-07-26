"""Tests for the auth-provider registry."""

from __future__ import annotations

import pytest

from aethis_cli import auth_providers
from aethis_cli.auth_providers import (
    ProviderContext,
    UnknownAuthMode,
    get_provider,
    known_modes,
    register_provider,
)


@pytest.fixture(autouse=True)
def _isolate_provider_registry():
    """Give each test its own view of the process-global provider registry.

    The registry is filled from two directions, and both can leak into a test:
    this module registers stubs, and ``aethis_cli.main`` registers every
    installed plugin *at import time*. On a machine with the staff plugin
    installed, merely importing the CLI in some other test registers extra auth
    modes for the rest of the session -- which is why tests here could pass in
    isolation and fail in the full suite. Snapshot and restore closes both.
    """
    saved = dict(auth_providers._PROVIDERS)
    try:
        yield
    finally:
        auth_providers._PROVIDERS.clear()
        auth_providers._PROVIDERS.update(saved)


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
    # The staff plugin ships this mode, so on a machine that has it installed it
    # may already be registered. This test is about how an *unregistered* mode
    # is reported, so drop it first; the autouse fixture puts it back.
    auth_providers._PROVIDERS.pop("gcloud_id_token", None)

    with pytest.raises(UnknownAuthMode) as exc_info:
        get_provider("gcloud_id_token")
    msg = str(exc_info.value)
    assert "gcloud_id_token" in msg
    assert "api_key" in msg  # lists registered modes
    assert "aethis-cli-internal" in msg


def test_unknown_mode_message_lists_plugin_registered_modes() -> None:
    # The inverse of the test above, and the regression guard for it: with a
    # plugin's mode present, an unknown mode must still report correctly and
    # advertise the plugin mode as available.
    register_provider("gcloud_id_token", lambda ctx: {"Authorization": "Bearer x"})

    with pytest.raises(UnknownAuthMode) as exc_info:
        get_provider("no_plugin_registers_this")
    msg = str(exc_info.value)
    assert "no_plugin_registers_this" in msg
    assert "gcloud_id_token" in msg


def test_register_and_lookup_plugin_provider() -> None:
    def stub_provider(ctx: ProviderContext) -> dict[str, str]:
        return {"Authorization": f"Bearer fake-token-for-{ctx.profile.get('audience')}"}

    register_provider("test_provider", stub_provider)
    assert "test_provider" in known_modes()
    headers = get_provider("test_provider")(_ctx(audience="aud-1"))
    assert headers == {"Authorization": "Bearer fake-token-for-aud-1"}


def test_re_register_replaces_provider() -> None:
    register_provider("api_key", lambda ctx: {"X-Other": "x"})
    assert get_provider("api_key")(_ctx()) == {"X-Other": "x"}
