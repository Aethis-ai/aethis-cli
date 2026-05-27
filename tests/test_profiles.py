"""Profile machinery + backwards-compat for the legacy single-key credentials file."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from aethis_cli import config
from aethis_cli.auth_helpers import RUNTIME, resolve_cached_key, is_anonymous_active
from aethis_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ~/.config/aethis to a temp dir and clear all env shorthands."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AETHIS_API_KEY", raising=False)
    monkeypatch.delenv("AETHIS_BASE_URL", raising=False)
    monkeypatch.delenv("AETHIS_PROFILE", raising=False)
    # Reset the runtime singleton so a stale --profile from another test
    # doesn't bleed into the resolver.
    RUNTIME.no_prompt = False
    RUNTIME.api_key_override = None
    RUNTIME.base_url_override = None
    RUNTIME.profile_override = None
    return tmp_path


def _write_legacy_single_key(tmp_path: Path, api_key: str) -> Path:
    """Write a pre-profile credentials file ({api_key: ...}) at the XDG path."""
    creds_dir = tmp_path / "aethis"
    creds_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    creds = creds_dir / "credentials"
    fd = os.open(str(creds), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(yaml.dump({"api_key": api_key}))
    return creds


def test_legacy_single_key_loads_as_default_profile(tmp_path: Path) -> None:
    _write_legacy_single_key(tmp_path, "ak_legacy_xyz")

    creds = config.load_credentials()
    assert creds["active_profile"] == "default"
    assert creds["profiles"]["default"]["api_key"] == "ak_legacy_xyz"


def test_legacy_single_key_resolves_for_default_profile(tmp_path: Path) -> None:
    _write_legacy_single_key(tmp_path, "ak_legacy_xyz")

    assert resolve_cached_key() == "ak_legacy_xyz"


def test_save_upgrades_legacy_to_multi_profile(tmp_path: Path) -> None:
    creds_path = _write_legacy_single_key(tmp_path, "ak_legacy_xyz")

    config.set_profile("new-dev", api_key="ak_test_abc")
    raw = yaml.safe_load(creds_path.read_text())
    assert raw["active_profile"] == "default"
    assert raw["profiles"]["default"]["api_key"] == "ak_legacy_xyz"
    assert raw["profiles"]["new-dev"]["api_key"] == "ak_test_abc"


def test_anonymous_profile_yields_no_key(tmp_path: Path) -> None:
    _write_legacy_single_key(tmp_path, "ak_legacy_xyz")
    config.set_active_profile("anonymous")

    assert is_anonymous_active() is True
    assert resolve_cached_key() is None


def test_profile_override_beats_sticky_default(tmp_path: Path) -> None:
    config.set_profile("admin", api_key="ak_admin")
    config.set_profile("new-dev", api_key="ak_dev")
    config.set_active_profile("admin")

    RUNTIME.profile_override = "new-dev"
    try:
        assert config.active_profile_name() == "new-dev"
        assert resolve_cached_key() == "ak_dev"
    finally:
        RUNTIME.profile_override = None


def test_env_api_key_beats_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config.set_profile("admin", api_key="ak_admin")
    config.set_active_profile("admin")
    monkeypatch.setenv("AETHIS_API_KEY", "ak_env_override")

    # AETHIS_API_KEY remains the absolute override, even with a configured profile.
    assert resolve_cached_key() == "ak_env_override"


def test_reserved_anonymous_name_rejected_at_set(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError):
        config.set_profile("anonymous", api_key="ak_should_not_save")


def test_remove_profile_resets_active_when_active(tmp_path: Path) -> None:
    config.set_profile("new-dev", api_key="ak_dev")
    config.set_active_profile("new-dev")

    config.remove_profile("new-dev")
    assert config.active_profile_name() == "default"


def test_set_profile_writes_auth_mode_and_audience(tmp_path: Path) -> None:
    config.set_profile(
        "internal-staging",
        base_url="https://aethis-core-internal-staging.run.app",
        auth_mode="gcloud_id_token",
        audience="https://aethis-core-internal-staging.run.app",
    )
    creds = config.load_credentials()
    profile = creds["profiles"]["internal-staging"]
    assert profile["base_url"] == "https://aethis-core-internal-staging.run.app"
    assert profile["auth_mode"] == "gcloud_id_token"
    assert profile["audience"] == "https://aethis-core-internal-staging.run.app"
    # Crucially, no api_key was written — that's the whole point.
    assert "api_key" not in profile


def test_set_profile_preserves_existing_auth_mode_when_unspecified(tmp_path: Path) -> None:
    config.set_profile("staff", base_url="https://foo", auth_mode="gcloud_id_token")
    # Now update only the base_url; auth_mode should stick.
    config.set_profile("staff", base_url="https://bar")
    profile = config.get_profile("staff")
    assert profile["auth_mode"] == "gcloud_id_token"
    assert profile["base_url"] == "https://bar"


def test_legacy_profile_without_auth_mode_defaults_to_api_key(tmp_path: Path) -> None:
    _write_legacy_single_key(tmp_path, "ak_legacy")
    profile = config.get_profile("default")
    # ``auth_mode`` is implicit — readers default to ``"api_key"`` when absent.
    assert profile.get("auth_mode") in (None, "api_key")


def test_profile_list_command_marks_active(tmp_path: Path) -> None:
    config.set_profile("admin", api_key="ak_admin_with_long_suffix_xyz")
    config.set_active_profile("admin")

    result = runner.invoke(app, ["profile", "list"])
    assert result.exit_code == 0
    # Active marker, masked key, and the reserved 'anonymous' slot all visible.
    assert "* " in result.output
    assert "admin" in result.output
    assert "anonymous" in result.output
    assert "ak_admi" in result.output  # mask shows first 7 chars
    assert "ak_admin_with_long_suffix_xyz" not in result.output  # full key never printed
