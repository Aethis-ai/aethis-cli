"""Load aethis.yaml and resolve API keys."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

from aethis_cli.errors import ConfigError, ProjectNotFound

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


def _reject_unsafe_url_parts(url: str) -> None:
    """Refuse userinfo, query, fragment, control or non-ASCII characters in a configured server URL.

    Userinfo would make every request (even anonymous ones) carry ``Authorization: Basic``
    to that host. The URL is never echoed.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        bad = parts.username is not None or parts.password is not None or "@" in parts.netloc
    except ValueError:
        raise ConfigError("Invalid server URL: not a well-formed URL.") from None
    if bad or parts.query or parts.fragment or "?" in url or "#" in url or not url.isprintable() or not url.isascii():
        raise ConfigError(
            "Invalid server URL: must not contain credentials, a query, a fragment or control characters."
        )


@dataclass
class ProjectConfig:
    project: str
    api_key_env: str = "AETHIS_API_KEY"
    anthropic_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str = DEFAULT_BASE_URL
    project_id: Optional[str] = None
    config_path: Path = field(default_factory=lambda: Path.cwd())
    deepseek_key_env: str = "DEEPSEEK_API_KEY"
    project_base_url: Optional[str] = None  # the project file's own value, exactly as written


def resolve_base_url_with_source() -> tuple[str, str]:
    """Return (base_url, source) where source is 'env', 'yaml', 'profile', or 'default'.

    For ANONYMOUS reads only (public catalogue, no credential). Resolution order:
      AETHIS_BASE_URL env var > aethis.yaml > active profile > DEFAULT_BASE_URL

    The project file may choose the host here, so nothing that carries a key,
    provider key or sign-in token may use this: go through
    :func:`authorize_credential_server` / :func:`resolve_credential_base_url`.
    """
    env = os.environ.get("AETHIS_BASE_URL")
    if env:
        _reject_unsafe_url_parts(env)
        return env, "env"
    try:
        cfg = load_project_config()
        if cfg.project_base_url and cfg.project_base_url != DEFAULT_BASE_URL:
            return cfg.project_base_url, "yaml"
    except ProjectNotFound:
        pass
    profile = get_profile(active_profile_name())
    if profile.get("base_url"):
        return profile["base_url"], "profile"
    return DEFAULT_BASE_URL, "default"


_HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")


def _is_local_host(host: str) -> bool:
    import ipaddress

    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_credential_base_url(raw: str) -> str:
    """Return the canonical form of a server URL, or raise :class:`ConfigError`.

    Strict by design — it never rewrites an invalid URL into a different one:
    the authority is rebuilt from the parsed parts and must equal the original
    (only an explicit default port may be dropped). Requires an http(s) scheme
    and a host; refuses control characters, non-ASCII, userinfo, query,
    fragment and a malformed or out-of-range port; allows plain http only for
    loopback hosts (``localhost``, ``127.0.0.0/8``, ``::1``). Canonical form:
    lowercase scheme and host, IPv6 bracketed, default port dropped, path kept
    as written with the trailing slash stripped.

    Error text never includes the raw URL (it may carry credentials or tokens).
    Some checks overlap and are kept for clearer messages, not as independent
    coverage: urlsplit already lowercases scheme and host; the round-trip also
    catches userinfo, an empty port and a leading-zero port; the final httpx
    parse is defence in depth behind the ASCII check.
    """
    import httpx
    from urllib.parse import urlsplit

    def bad(why: str) -> ConfigError:
        return ConfigError(f"Invalid server URL: {why}.")

    if not isinstance(raw, str):
        raise bad("must be a string")
    if not raw.isprintable() or not raw.isascii() or " " in raw:
        raise bad("contains whitespace, control or non-ASCII characters")
    try:
        parts = urlsplit(raw)
        scheme = parts.scheme.lower()
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise bad("not a well-formed URL") from None
    if scheme not in ("http", "https"):
        raise bad("must start with http:// or https://")
    if "@" in parts.netloc:
        raise bad("must not contain credentials")
    if "%" in parts.netloc:
        raise bad("host must not be percent-encoded")
    if "?" in raw or "#" in raw:
        raise bad("must not contain a query or fragment")
    if not host:
        raise bad("missing host")
    host = host.lower()
    shown_host = f"[{host}]" if ":" in host else host
    if not _HOST_RE.fullmatch(host) and ":" not in host:
        raise bad("host contains invalid characters")
    authority = shown_host if port is None else f"{shown_host}:{port}"
    if parts.netloc.lower() != authority:
        raise bad("host or port is malformed")
    if port is not None and not 1 <= port <= 65535:
        raise bad("port out of range")
    if scheme == "http" and not _is_local_host(host):
        raise ConfigError(
            f"Refusing to use HTTP for remote host '{host}'. Use HTTPS or target localhost for local development."
        )
    if port is not None and (scheme, port) in {("https", 443), ("http", 80)}:
        authority = shown_host
    canonical = f"{scheme}://{authority}{parts.path.rstrip('/')}"
    try:
        httpx.URL(canonical)
    except (httpx.InvalidURL, ValueError):
        raise bad("rejected by the HTTP client") from None
    return canonical


def profile_effective_base_url(profile_name: str) -> str:
    """The canonical server a profile names (default if unset); raises if its URL is invalid."""
    return parse_credential_base_url(get_profile(profile_name).get("base_url") or DEFAULT_BASE_URL)


def resolve_credential_base_url(profile_name: Optional[str] = None) -> tuple[str, str]:
    """Return (canonical_base_url, source) for commands that send or mint credentials.

    Order: ``AETHIS_BASE_URL`` (also set by ``--base-url``) > the profile's
    ``base_url`` > the default. Unlike :func:`resolve_base_url_with_source`
    this NEVER consults a project ``aethis.yaml``: a file found by walking up
    from the working directory must not choose where sign-in tokens or newly
    minted keys are sent. Every source goes through
    :func:`parse_credential_base_url`. ``source`` is 'env', 'profile' or 'default'.
    """
    env = os.environ.get("AETHIS_BASE_URL")
    if env:
        return parse_credential_base_url(env), "env"
    profile = get_profile(profile_name or active_profile_name())
    if profile.get("base_url"):
        return parse_credential_base_url(profile["base_url"]), "profile"
    return parse_credential_base_url(DEFAULT_BASE_URL), "default"


_PROJECT_SERVER_REFUSED = (
    "The server this project selects (aethis.yaml base_url) is not the one your active profile '{p}' uses, "
    "so no credential was sent. To use it, set AETHIS_BASE_URL (or pass --base-url) to that server, or "
    "select or create a profile whose base_url is that server (`aethis profile add <name> --base-url <url>`)."
)


def authorize_credential_server(requested_url: Optional[str] = None, profile_name: Optional[str] = None) -> str:
    """The single decision: may a credential go to ``requested_url``? Returns the server to use.

    A credential (API key, provider key, sign-in token) goes only to the server
    the USER selected: ``AETHIS_BASE_URL`` (also set by ``--base-url``) > the
    profile's ``base_url`` > the default. ``requested_url`` is whatever the
    caller was about to use (e.g. a project file's ``base_url``); if it is not
    the same server in canonical form this raises BEFORE anything is sent, and
    never echoes it (it is untrusted). Every credential-bearing request — client
    construction, inline login, the 401 refresh, the browser sign-in itself —
    calls this, so there is one rule rather than a check per call site.
    """
    name = profile_name or active_profile_name()
    trusted, _ = resolve_credential_base_url(name)
    # The project file's OWN value is always compared (read here, not taken from
    # a caller), before any env/profile override could mask it: a project that
    # names another server is refused unless the user's own server equals it.
    for candidate in (requested_url, _read_project_base_url()):
        if candidate is None:
            continue
        try:
            requested = parse_credential_base_url(candidate)
        except ConfigError as e:
            raise ConfigError(f"The project's server is not usable for credentials: {e}") from None
        if requested != trusted:
            raise ConfigError(_PROJECT_SERVER_REFUSED.format(p=name))
    return trusted


def _read_project_raw() -> Optional[dict]:
    """The working directory's project file, parsed, or None if there is none.

    A file that exists but cannot be parsed is an error, never "no project".
    """
    try:
        path = _find_config(Path.cwd())
    except ProjectNotFound:
        return None
    try:
        raw = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError) as e:
        raise ConfigError(f"Cannot read the project file {path} ({type(e).__name__}).") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"The project file {path} must be a YAML mapping.")
    return raw


def _read_project_base_url() -> Optional[str]:
    """The working directory's project file's ``base_url`` exactly as written, or None."""
    raw = _read_project_raw()
    value = raw.get("base_url") if raw else None
    # Subsumed, kept for the clearer message: a non-string value would also be refused by
    # parse_credential_base_url in authorize_credential_server.
    if value is not None and not isinstance(value, str):
        raise ConfigError("The project's base_url must be a string.")
    return value


def check_project_api_key_env() -> None:
    """Refuse a project file's non-default ``api_key_env`` the user has not designated.

    Called from the one key resolution, so no path that resolves a key can skip it.
    """
    raw = _read_project_raw()
    if raw:
        _designated_env_name("AETHIS_API_KEY_ENV", raw.get("api_key_env", "AETHIS_API_KEY"), "AETHIS_API_KEY")


def project_credential_server() -> str:
    """Server for a credential-bearing command that has no ProjectConfig of its own.

    Applies :func:`authorize_credential_server` to the working directory's
    project file, if it has a usable one — so such a command refuses in a
    project that selects another server exactly as the project commands do.
    """
    try:
        load_project_config()  # a project file that exists but is invalid is an error here too
    except ProjectNotFound:
        pass
    return authorize_credential_server()


_SAVE_REMEDY = {
    "generate": "Use --no-save, set the profile's server (`aethis profile add {p} --base-url {u}`), "
    "or unset AETHIS_BASE_URL.",
    "login": "Set the profile's server (`aethis profile add {p} --base-url {u}`), "
    "unset AETHIS_BASE_URL, or pick a matching profile with --profile.",
}


def check_save_target(base_url: str, source: str, profile_name: str, command: str = "generate") -> None:
    """Refuse to save a key minted on a server the target profile does not name.

    Only an environment-supplied server can disagree with the profile (the
    other sources are the profile's own server). Canonical forms are compared; an invalid URL on either side raises.
    """
    if profile_name == ANONYMOUS_PROFILE:
        raise ConfigError(
            f"Cannot save a key to the reserved '{ANONYMOUS_PROFILE}' profile. Pick a different profile with --profile."
        )
    if source != "env":
        return
    effective = profile_effective_base_url(profile_name)
    if parse_credential_base_url(base_url) != effective:
        remedy = _SAVE_REMEDY[command].format(p=profile_name, u=base_url)
        raise ConfigError(
            f"AETHIS_BASE_URL ({base_url}) differs from profile '{profile_name}' ({effective}); "
            f"the new key would be saved against the wrong server. {remedy}"
        )


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

    base_url = authorize_credential_server(base_url)
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
    from aethis_cli.auth_helpers import is_anonymous_active, require_auth_or_login_inline
    from aethis_cli.client import make_anonymous_client

    try:
        cfg = load_project_config()
    except ProjectNotFound:
        base_url, _ = resolve_credential_base_url()
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
    from aethis_cli.auth_helpers import resolve_cached_key, is_anonymous_active
    from aethis_cli.client import make_anonymous_client

    try:
        cfg = load_project_config()
    except ProjectNotFound:
        base_url, _ = resolve_credential_base_url()
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

    api_key = resolve_cached_key()
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

    # The server this project talks to: AETHIS_BASE_URL (user's choice) >
    # aethis.yaml > the active profile's server > the default. A yaml value that
    # differs from the user's own server is honoured for anonymous reads only;
    # every credential-bearing use is refused by authorize_credential_server.
    project_url = raw.get("base_url")
    if project_url is not None and not isinstance(project_url, str):
        raise ConfigError(f"base_url in {yaml_path} must be a string")
    base_url = os.environ.get("AETHIS_BASE_URL") or project_url
    if base_url:
        _reject_unsafe_url_parts(base_url)
        _validate_base_url(base_url)
    else:
        base_url = get_profile(active_profile_name()).get("base_url") or DEFAULT_BASE_URL

    return ProjectConfig(
        project=raw["project"],
        api_key_env=raw.get("api_key_env", "AETHIS_API_KEY"),
        anthropic_key_env=raw.get("anthropic_key_env", "ANTHROPIC_API_KEY"),
        deepseek_key_env=raw.get("deepseek_key_env", "DEEPSEEK_API_KEY"),
        base_url=base_url,
        project_id=project_id,
        config_path=project_dir,
        project_base_url=project_url,
    )


_ENV_REFUSED = (
    "aethis.yaml names a non-default key variable that you have not designated, so it was not read. Environment "
    "variables are read as keys only if you name them yourself: set {setting} to the variable name in your own "
    "environment."
)


def _designated_env_name(setting: str, project_value: str, default: str) -> str:
    """The environment variable the USER designated for a key, or refuse.

    A project file can name any variable (``aethis.yaml`` is copied between
    machines), so its value is honoured only when it is the default or equals
    the user's own ``setting``; anything else raises WITHOUT reading it. With no
    project value the user's ``setting`` (if any) applies, else the default.
    """
    designated = os.environ.get(setting) or None
    if project_value != default and project_value != designated:
        raise ConfigError(_ENV_REFUSED.format(setting=setting))
    return designated or default


def designated_api_key_env() -> str:
    """Name of the environment variable the USER designated for the Aethis key."""
    return os.environ.get("AETHIS_API_KEY_ENV") or "AETHIS_API_KEY"


def resolve_api_key(config: ProjectConfig) -> str:
    """Resolve API key: env var → active profile → keychain (default only) → lazy-auth.

    See :func:`aethis_cli.auth_helpers.resolve_cached_key` for the resolution
    chain. When no cached key is found we delegate to the lazy-auth helper,
    which will offer an inline browser sign-in on a TTY or raise
    ``AuthRequired`` on non-interactive shells / ``--no-prompt``.
    """
    from aethis_cli.auth_helpers import resolve_cached_key, require_auth_or_login_inline

    # A project file's api_key_env is refused unless the user designated it. This is
    # subsumed by resolve_cached_key's check for the cwd's project; kept for a cfg that
    # was loaded from elsewhere.
    _designated_env_name("AETHIS_API_KEY_ENV", config.api_key_env, "AETHIS_API_KEY")
    authorize_credential_server(config.base_url)

    cached = resolve_cached_key()
    if cached:
        return cached

    return require_auth_or_login_inline(config.base_url)


def resolve_anthropic_key(config: ProjectConfig) -> Optional[str]:
    """Resolve the Anthropic key from the variable the user designated. Returns None if not set.

    Default ``ANTHROPIC_API_KEY``; ``AETHIS_ANTHROPIC_KEY_ENV`` names another.
    A different ``anthropic_key_env`` in a project file is refused unread.
    """
    name = _designated_env_name("AETHIS_ANTHROPIC_KEY_ENV", config.anthropic_key_env, "ANTHROPIC_API_KEY")
    return os.environ.get(name) or None


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
    raise ProjectNotFound(f"No aethis.yaml found in {start} or any parent directory.")


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


def resolve_deepseek_key(config: ProjectConfig) -> Optional[str]:
    """Read the generation-only DeepSeek credential from the variable the user designated.

    Default ``DEEPSEEK_API_KEY``; ``AETHIS_DEEPSEEK_KEY_ENV`` names another.
    """
    name = _designated_env_name("AETHIS_DEEPSEEK_KEY_ENV", config.deepseek_key_env, "DEEPSEEK_API_KEY")
    return os.environ.get(name) or None
