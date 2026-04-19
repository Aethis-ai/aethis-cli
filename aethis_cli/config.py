"""Load aethis.yaml and resolve API keys."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from aethis_cli.errors import ConfigError

DEFAULT_BASE_URL = "https://api.aethis.ai"

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
    """Resolve API key: env var → OS keychain → credentials file."""
    key = os.environ.get(config.api_key_env)
    if key:
        return key

    # Try OS keychain (if keyring is installed)
    try:
        import keyring

        key = keyring.get_password("aethis-cli", "api_key")
        if key:
            return key
    except Exception:
        pass

    # Fall back to plaintext credentials file
    creds_path = credentials_path()
    if creds_path.exists():
        raw = yaml.safe_load(creds_path.read_text()) or {}
        key = raw.get("api_key")
        if key:
            return key

    raise ConfigError(f"API key not found. Set ${config.api_key_env} or run 'aethis login'.")


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
