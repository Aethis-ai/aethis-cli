"""Background update-check banner for the aethis CLI.

Prints a one-line "newer release available" banner to stderr at exit
when a newer version is on PyPI. Modelled on `gh`'s notify-only DX:
non-blocking, never errors loudly, and easy to disable.

Disable with `AETHIS_DISABLE_UPDATE_CHECK=1`. Also auto-skipped when
stderr is not a TTY (CI / piped output) and on `--version` / `--help`
short-circuits (the caller decides when to invoke this).
"""

from __future__ import annotations

import atexit
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

_CACHE_TTL_SECONDS = 24 * 60 * 60
_PYPI_TIMEOUT_SECONDS = 3.0
_JOIN_TIMEOUT_SECONDS = 0.05
_DISABLE_ENV = "AETHIS_DISABLE_UPDATE_CHECK"


def _config_dir() -> Path:
    """Return ~/.config/aethis (XDG-aware). Mirrors config.credentials_path."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "aethis"
    return Path.home() / ".config" / "aethis"


def _cache_path() -> Path:
    return _config_dir() / "update_check.json"


def _load_cache() -> Optional[dict]:
    try:
        raw = _cache_path().read_text()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _save_cache(latest: str) -> None:
    payload = {"checked_at": int(time.time()), "latest": latest}
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    except OSError:
        pass


def _fetch_latest_pypi(package: str) -> Optional[str]:
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        resp = httpx.get(url, timeout=_PYPI_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    info = data.get("info") or {}
    latest = info.get("version")
    if isinstance(latest, str) and latest:
        return latest
    return None


_VERSION_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _parse_version(v: str) -> tuple[int, int, int]:
    """Loose semver-ish tuple. Suffixes (a/b/rc/dev) sort lower at same triple."""
    m = _VERSION_RE.match(v.strip())
    if not m:
        return (0, 0, 0)
    return (int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0))


def _is_newer(latest: str, current: str) -> bool:
    if not latest or not current:
        return False
    if latest == current:
        return False
    return _parse_version(latest) > _parse_version(current)


def _detect_install_method(package: str) -> tuple[str, str]:
    """Return (method, upgrade_command). Heuristic on sys.executable."""
    exe = (sys.executable or "").lower()
    prefix = (sys.prefix or "").lower()
    if "/uv/tools/" in exe or "/uv/tools/" in prefix:
        return ("uv", f"uv tool install --upgrade {package}")
    if "/pipx/venvs/" in exe or "/pipx/venvs/" in prefix:
        return ("pipx", f"pipx upgrade {package}")
    return ("pip", f"pip install --upgrade {package}")


def _print_banner(package: str, current: str, latest: str) -> None:
    _, command = _detect_install_method(package)
    msg = (
        f"\nA new release of {package} is available: {current} → {latest}\n"
        f"To upgrade, run: {command}\n"
    )
    try:
        sys.stderr.write(msg)
        sys.stderr.flush()
    except OSError:
        pass


def _should_skip() -> bool:
    if os.environ.get(_DISABLE_ENV):
        return True
    try:
        if not sys.stderr.isatty():
            return True
    except (AttributeError, ValueError):
        return True
    return False


def _check_worker(package: str, holder: dict) -> None:
    cache = _load_cache()
    now = int(time.time())
    if cache and isinstance(cache.get("checked_at"), int):
        if now - cache["checked_at"] < _CACHE_TTL_SECONDS:
            latest = cache.get("latest")
            if isinstance(latest, str):
                holder["latest"] = latest
            return
    latest = _fetch_latest_pypi(package)
    if latest:
        _save_cache(latest)
        holder["latest"] = latest


def start_background_check(package: str, current_version: str) -> None:
    """Kick off a daemon thread that checks PyPI; show banner at exit."""
    if _should_skip():
        return

    holder: dict = {}
    thread = threading.Thread(
        target=_check_worker,
        args=(package, holder),
        daemon=True,
        name="aethis-update-check",
    )
    thread.start()

    def _on_exit() -> None:
        thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
        latest = holder.get("latest")
        if not isinstance(latest, str):
            return
        if _is_newer(latest, current_version):
            _print_banner(package, current_version, latest)

    atexit.register(_on_exit)
