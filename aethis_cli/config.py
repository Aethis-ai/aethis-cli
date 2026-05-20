"""Load aethis.yaml and resolve API keys."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

from aethis_cli.errors import ConfigError

if TYPE_CHECKING:
    from aethis_cli.client import AethisClient

DEFAULT_BASE_URL = "https://api.aethis.ai"
DEFAULT_PROFILE = "default"
ANONYMOUS_PROFILE = "anonymous"

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"}


def _validate_base_url(url: str) -> None:
    """Reject http:// URLs unless targeting localhost (local dev)."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
        raise ConfigError(
            f"Refusing to use HTTP for remote host '{parsed.hostname}'. "
            "Use HTTPS or target localhost for local development."
        )


@dataclass
class ProjectConfig:
    project: str
    api_key_env: str = "AETHIS_API_KEY"
    anthropic_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str = DEFAULT_BASE_URL
    project_id: Optional[str] = None
    config_path: Path = field(default_factory=lambda: Path.cwd())


def resolve_base_url_with_source() -> tuple[str, str]:
    """Return (base_url, source) where source is 'env', 'yaml', 'profile', or 'default'.

    Resolution order matches what every read-only command sees:
      AETHIS_BASE_URL env var > aethis.yaml > active profile > DEFAULT_BASE_URL
    """
    env = os.environ.get("AETHIS_BASE_URL")
    if env:
        return env, "env"
    try:
        cfg = load_project_config()
        if cfg.base_url != DEFAULT_BASE_URL:
            return cfg.base_url, "yaml"
    except ConfigError:
        pass
    profile = get_profile(active_profile_name())
    if profile.get("base_url"):
        return profile["base_url"], "profile"
    return DEFAULT_BASE_URL, "default"


def make_authed_client(
    api_key: str,
    base_url: str,
    *,
    anthropic_key: Optional[str] = None,
    profile: Optional[dict] = None,
) -> "AethisClient":
    """Build an :class:`AethisClient` for the active profile's auth mode.

    For the default ``api_key`` mode this wires the lazy-auth refresh hook so
    a 401 from the server transparently triggers the inline browser sign-in
    flow once before failing. For any other mode (e.g. ``gcloud_id_token``
    contributed by ``aethis-cli-internal``) the corresponding provider is
    selected from the registry and the refresh hook is left unset — those
    providers handle their own token lifetime.
    """
    from aethis_cli.auth_helpers import require_auth_or_login_inline
    from aethis_cli.auth_providers import get_provider
    from aethis_cli.client import AethisClient

    auth_mode = (profile or {}).get("auth_mode", "api_key")
    auth_provider = get_provider(auth_mode)

    on_auth_required = None
    if auth_mode == "api_key":

        def _refresh(force_browser: bool = True) -> str:
            return require_auth_or_login_inline(base_url, force_browser=force_browser)

        on_auth_required = _refresh

    return AethisClient(
        api_key,
        base_url,
        anthropic_key=anthropic_key,
        on_auth_required=on_auth_required,
        auth_provider=auth_provider,
        profile=profile,
    )


def load_client_or_fallback() -> tuple["ProjectConfig", "AethisClient"]:
    """Load project config if available, else fall back to DEFAULT_BASE_URL.

    Used by read-only commands (`explain`, `decide`, `rulesets`, `projects`,
    `whoami`) so they work from any directory. Authentication is lazy: if no
    API key is cached the client is built with a key-refresh hook that runs
    the inline browser login on the first 401. This lets a fresh user run
    ``aethis projects list`` and complete sign-in without backing out to
    ``aethis login`` and re-running.

    When the active profile is ``anonymous`` the function returns an unsigned
    client immediately — no lazy-auth, no browser prompt.
    """
    from aethis_cli.auth_helpers import RUNTIME, is_anonymous_active, require_auth_or_login_inline
    from aethis_cli.client import make_anonymous_client

    try:
        cfg = load_project_config()
    except ConfigError:
        base_url, _ = resolve_base_url_with_source()
        if RUNTIME.base_url_override:
            base_url = RUNTIME.base_url_override
        _validate_base_url(base_url)
        cfg = ProjectConfig(project="", base_url=base_url)

    if is_anonymous_active():
        return cfg, make_anonymous_client(cfg.base_url)

    profile = get_profile(active_profile_name())
    if (profile.get("auth_mode") or "api_key") == "api_key":
        api_key = require_auth_or_login_inline(cfg.base_url)
    else:
        # Non-api_key modes (e.g. gcloud_id_token) don't need a cached key —
        # the provider mints its own credential at request time.
        api_key = ""
    return cfg, make_authed_client(api_key, cfg.base_url, profile=profile)


def load_client_or_anon() -> tuple["ProjectConfig", "AethisClient"]:
    """Like load_client_or_fallback but never prompts for sign-in.

    Used by read-only public-endpoint commands (decide, explain, fields).
    If a key is cached or set via env/flag, use it — authenticated callers
    get access to their private rulesets. If no key is found, fall back to
    an unsigned client so public rulesets work with zero setup.
    """
    from aethis_cli.auth_helpers import RUNTIME, _resolve_cached_key, is_anonymous_active
    from aethis_cli.client import make_anonymous_client

    try:
        cfg = load_project_config()
    except ConfigError:
        base_url, _ = resolve_base_url_with_source()
        if RUNTIME.base_url_override:
            base_url = RUNTIME.base_url_override
        _validate_base_url(base_url)
        cfg = ProjectConfig(project="", base_url=base_url)

    if is_anonymous_active():
        return cfg, make_anonymous_client(cfg.base_url)

    profile = get_profile(active_profile_name())
    auth_mode = profile.get("auth_mode") or "api_key"
    if auth_mode != "api_key":
        # Profile selects a non-api_key auth scheme (e.g. gcloud_id_token).
        # The provider is responsible for minting its own credential; we
        # don't need a cached API key, and we shouldn't fall back to
        # anonymous just because one isn't present.
        return cfg, make_authed_client("", cfg.base_url, profile=profile)

    api_key = _resolve_cached_key()
    if api_key is None:
        return cfg, make_anonymous_client(cfg.base_url)

    return cfg, make_authed_client(api_key, cfg.base_url, profile=profile)


def load_project_config(path: Optional[Path] = None) -> ProjectConfig:
    """Load aethis.yaml. Walks up the directory tree if no explicit path given."""
    if path and path.is_file():
        yaml_path = path
    else:
        yaml_path = _find_config(path or Path.cwd())

    try:
        raw = yaml.safe_load(yaml_path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {yaml_path}: {e}")
    if "project" not in raw:
        raise ConfigError(f"Missing 'project' key in {yaml_path}")

    project_dir = yaml_path.parent
    project_id = _read_state_field(project_dir, "project_id")

    # AETHIS_BASE_URL env var overrides aethis.yaml (useful for local dev)
    base_url = os.environ.get("AETHIS_BASE_URL") or raw.get("base_url", DEFAULT_BASE_URL)
    _validate_base_url(base_url)

    return ProjectConfig(
        project=raw["project"],
        api_key_env=raw.get("api_key_env", "AETHIS_API_KEY"),
        anthropic_key_env=raw.get("anthropic_key_env", "ANTHROPIC_API_KEY"),
        base_url=base_url,
        project_id=project_id,
        config_path=project_dir,
    )


def resolve_api_key(config: ProjectConfig) -> str:
    """Resolve API key: env var → active profile → keychain (default only) → lazy-auth.

    See :func:`aethis_cli.auth_helpers._resolve_cached_key` for the resolution
    chain. When no cached key is found we delegate to the lazy-auth helper,
    which will offer an inline browser sign-in on a TTY or raise
    ``AuthRequired`` on non-interactive shells / ``--no-prompt``.
    """
    from aethis_cli.auth_helpers import _resolve_cached_key, require_auth_or_login_inline

    cached = _resolve_cached_key()
    if cached:
        # Honour the project's ``api_key_env`` override only when set to the
        # non-default name — the standard ``AETHIS_API_KEY`` path is already
        # covered by ``_resolve_cached_key``.
        if config.api_key_env != "AETHIS_API_KEY":
            override = os.environ.get(config.api_key_env)
            if override:
                return override
        return cached

    if config.api_key_env != "AETHIS_API_KEY":
        override = os.environ.get(config.api_key_env)
        if override:
            return override

    return require_auth_or_login_inline(config.base_url)


def resolve_anthropic_key(config: ProjectConfig) -> Optional[str]:
    """Resolve Anthropic API key from env var. Returns None if not set."""
    return os.environ.get(config.anthropic_key_env) or None


def write_state(config_path: Path, data: dict) -> None:
    """Write or merge into .aethis/state.json."""
    state_dir = config_path / ".aethis"
    state_dir.mkdir(exist_ok=True)
    state_file = state_dir / "state.json"
    existing = {}
    if state_file.exists():
        existing = json.loads(state_file.read_text())
    existing.update(data)
    state_file.write_text(json.dumps(existing, indent=2) + "\n")


def read_state(config_path: Path) -> dict:
    """Read .aethis/state.json, returning empty dict if missing."""
    state_file = config_path / ".aethis" / "state.json"
    if state_file.exists():
        return json.loads(state_file.read_text())
    return {}


def _find_config(start: Path) -> Path:
    """Walk up from start looking for aethis.yaml."""
    current = start.resolve()
    while True:
        candidate = current / "aethis.yaml"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    raise ConfigError(f"No aethis.yaml found in {start} or any parent directory.")


def _read_state_field(project_dir: Path, key: str) -> Optional[str]:
    state = read_state(project_dir)
    return state.get(key)


def credentials_path() -> Path:
    """Return the path to ~/.config/aethis/credentials (respects XDG_CONFIG_HOME)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "aethis" / "credentials"
    return Path.home() / ".config" / "aethis" / "credentials"


# -- Profiles -----------------------------------------------------------------
#
# The credentials file at ``credentials_path()`` stores one or more named
# profiles. The structure is::
#
#     active_profile: default
#     profiles:
#       default:
#         api_key: ak_live_...
#         base_url: https://api.aethis.ai
#       new-dev:
#         api_key: ak_test_...
#       internal-staging:                     # staff-only (aethis-cli-internal)
#         base_url: https://aethis-core-internal-staging-...run.app
#         auth_mode: gcloud_id_token
#         audience: https://aethis-core-internal-staging-...run.app
#       anonymous: {}
#
# Optional per-profile fields:
#   * ``api_key`` — the ``X-API-Key`` header value (default ``auth_mode``).
#   * ``base_url`` — overrides the global default for this profile.
#   * ``auth_mode`` — name of an auth provider registered via
#     :mod:`aethis_cli.auth_providers`. Defaults to ``"api_key"``.
#   * ``audience`` — used by audience-scoped providers (e.g. GCP ID tokens).
#
# The reserved name ``anonymous`` is recognised even when not present in the
# file: selecting it yields no API key (and the client runs in unsigned mode).
#
# Backwards compatibility: legacy single-key files of the shape
# ``{api_key: ...}`` are read as if they declared ``profiles.default.api_key``.
# The first save after a legacy read upgrades the file to the new format.


def _normalize_credentials(raw: object) -> dict:
    """Coerce a raw YAML payload into ``{active_profile, profiles}``.

    Handles three shapes:
    * Legacy single-key ``{api_key: ...}`` → treated as the default profile.
    * New multi-profile ``{active_profile, profiles: {...}}`` → returned as-is
      with missing fields filled in.
    * Anything else (None, malformed) → empty multi-profile skeleton.
    """
    if not isinstance(raw, dict):
        return {"active_profile": DEFAULT_PROFILE, "profiles": {}}

    if "profiles" in raw and isinstance(raw["profiles"], dict):
        active = raw.get("active_profile") or DEFAULT_PROFILE
        return {"active_profile": str(active), "profiles": dict(raw["profiles"])}

    # Legacy: bare api_key (and maybe base_url) at the top level.
    legacy_default: dict = {}
    if "api_key" in raw and raw["api_key"]:
        legacy_default["api_key"] = raw["api_key"]
    if "base_url" in raw and raw["base_url"]:
        legacy_default["base_url"] = raw["base_url"]
    profiles = {DEFAULT_PROFILE: legacy_default} if legacy_default else {}
    return {"active_profile": DEFAULT_PROFILE, "profiles": profiles}


def load_credentials() -> dict:
    """Read the credentials file and return a normalised ``{active_profile, profiles}``.

    Returns the empty skeleton if the file is missing or unreadable.
    """
    path = credentials_path()
    if not path.exists():
        return {"active_profile": DEFAULT_PROFILE, "profiles": {}}
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        return {"active_profile": DEFAULT_PROFILE, "profiles": {}}
    return _normalize_credentials(raw)


def save_credentials(data: dict) -> None:
    """Atomically write the credentials file with mode 0600."""
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(yaml.dump(data, sort_keys=False))


def active_profile_name() -> str:
    """Return the name of the active profile.

    Resolution order: ``--profile`` flag > ``AETHIS_PROFILE`` env >
    ``active_profile`` field in credentials > ``"default"``.
    """
    from aethis_cli.auth_helpers import RUNTIME

    if RUNTIME.profile_override:
        return RUNTIME.profile_override
    env = os.environ.get("AETHIS_PROFILE")
    if env:
        return env
    return load_credentials().get("active_profile") or DEFAULT_PROFILE


def get_profile(name: str) -> dict:
    """Return the profile dict for ``name`` (or empty dict if not present)."""
    creds = load_credentials()
    return dict(creds["profiles"].get(name, {}))


def set_profile(
    name: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    auth_mode: Optional[str] = None,
    audience: Optional[str] = None,
) -> None:
    """Create or update a named profile in the credentials file.

    Only fields passed as non-None are written; existing fields are preserved.
    ``auth_mode`` selects an auth provider registered via
    :mod:`aethis_cli.auth_providers` (default ``"api_key"``); ``audience`` is
    consumed by audience-scoped providers like ``gcloud_id_token``.
    """
    if name == ANONYMOUS_PROFILE:
        raise ConfigError(
            f"Profile name '{ANONYMOUS_PROFILE}' is reserved — selecting it always "
            "uses no API key. Pick a different name."
        )
    creds = load_credentials()
    profile = dict(creds["profiles"].get(name, {}))
    if api_key is not None:
        profile["api_key"] = api_key
    if base_url is not None:
        profile["base_url"] = base_url
    if auth_mode is not None:
        profile["auth_mode"] = auth_mode
    if audience is not None:
        profile["audience"] = audience
    creds["profiles"][name] = profile
    save_credentials(creds)


def remove_profile(name: str) -> None:
    """Delete a profile. Raises ``ConfigError`` if it doesn't exist."""
    creds = load_credentials()
    if name not in creds["profiles"]:
        raise ConfigError(f"Profile '{name}' does not exist.")
    del creds["profiles"][name]
    if creds.get("active_profile") == name:
        creds["active_profile"] = DEFAULT_PROFILE
    save_credentials(creds)


def set_active_profile(name: str) -> None:
    """Set the sticky default profile name."""
    creds = load_credentials()
    creds["active_profile"] = name
    save_credentials(creds)
