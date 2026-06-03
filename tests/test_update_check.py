"""Tests for the update-check banner."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from aethis_cli import update_check as uc


@pytest.fixture
def fake_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the cache to a tmp dir; clear the disable flag."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv(uc._DISABLE_ENV, raising=False)
    return tmp_path / "aethis"


def test_is_newer_basic() -> None:
    assert uc._is_newer("0.10.0", "0.9.0") is True
    assert uc._is_newer("1.0.0", "0.99.99") is True
    assert uc._is_newer("0.9.0", "0.9.0") is False
    assert uc._is_newer("0.9.0", "0.10.0") is False
    assert uc._is_newer("", "0.9.0") is False
    assert uc._is_newer("0.9.0", "") is False


def test_parse_version_handles_garbage() -> None:
    assert uc._parse_version("not-a-version") == (0, 0, 0)
    assert uc._parse_version("1") == (1, 0, 0)
    assert uc._parse_version("1.2") == (1, 2, 0)
    assert uc._parse_version("1.2.3rc4") == (1, 2, 3)


def test_detect_install_method_uv() -> None:
    with patch.object(uc.sys, "executable", "/Users/x/.local/share/uv/tools/aethis-cli/bin/python"):
        method, cmd = uc._detect_install_method("aethis-cli")
    assert method == "uv"
    assert cmd == "uv tool upgrade aethis-cli"


def test_detect_install_method_pipx() -> None:
    with patch.object(uc.sys, "executable", "/Users/x/.local/pipx/venvs/aethis-cli/bin/python"):
        method, cmd = uc._detect_install_method("aethis-cli")
    assert method == "pipx"
    assert cmd == "pipx upgrade aethis-cli"


def test_detect_install_method_falls_back_to_pip() -> None:
    with patch.object(uc.sys, "executable", "/usr/local/bin/python3"), patch.object(uc.sys, "prefix", "/usr/local"):
        method, cmd = uc._detect_install_method("aethis-cli")
    assert method == "pip"
    assert cmd == "pip install --upgrade aethis-cli"


def test_save_and_load_cache(fake_config_dir: Path) -> None:
    uc._save_cache("1.2.3")
    cache = uc._load_cache()
    assert cache is not None
    assert cache["latest"] == "1.2.3"
    assert isinstance(cache["checked_at"], int)


def test_load_cache_missing_returns_none(fake_config_dir: Path) -> None:
    assert uc._load_cache() is None


def test_load_cache_corrupt_returns_none(fake_config_dir: Path) -> None:
    cache_path = uc._cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("{not json")
    assert uc._load_cache() is None


@respx.mock
def test_fetch_latest_pypi_happy_path() -> None:
    respx.get("https://pypi.org/pypi/aethis-cli/json").mock(
        return_value=httpx.Response(200, json={"info": {"version": "0.11.0"}})
    )
    assert uc._fetch_latest_pypi("aethis-cli") == "0.11.0"


@respx.mock
def test_fetch_latest_pypi_404_returns_none() -> None:
    respx.get("https://pypi.org/pypi/aethis-cli/json").mock(return_value=httpx.Response(404))
    assert uc._fetch_latest_pypi("aethis-cli") is None


@respx.mock
def test_fetch_latest_pypi_network_error_returns_none() -> None:
    respx.get("https://pypi.org/pypi/aethis-cli/json").mock(side_effect=httpx.ConnectError("boom"))
    assert uc._fetch_latest_pypi("aethis-cli") is None


def test_check_worker_uses_fresh_cache(fake_config_dir: Path) -> None:
    """Cache <24h old: no HTTP call, just read latest from cache."""
    uc._save_cache("0.99.0")
    holder: dict = {}
    with patch.object(uc, "_fetch_latest_pypi") as mock_fetch:
        uc._check_worker("aethis-cli", holder)
    mock_fetch.assert_not_called()
    assert holder["latest"] == "0.99.0"


def test_check_worker_skips_stale_cache(fake_config_dir: Path) -> None:
    """Cache >24h old: HTTP call refreshes it."""
    cache_path = uc._cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"checked_at": int(time.time()) - (uc._CACHE_TTL_SECONDS + 60), "latest": "0.5.0"})
    )
    holder: dict = {}
    with patch.object(uc, "_fetch_latest_pypi", return_value="0.99.0") as mock_fetch:
        uc._check_worker("aethis-cli", holder)
    mock_fetch.assert_called_once_with("aethis-cli")
    assert holder["latest"] == "0.99.0"
    refreshed = uc._load_cache()
    assert refreshed is not None
    assert refreshed["latest"] == "0.99.0"


def test_should_skip_when_env_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(uc._DISABLE_ENV, "1")
    assert uc._should_skip() is True


def test_should_skip_when_stderr_not_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(uc._DISABLE_ENV, raising=False)

    class _NotTty:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(uc.sys, "stderr", _NotTty())
    assert uc._should_skip() is True


def test_start_background_check_skips_when_disabled(fake_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disabled: no thread spawned, no atexit registered."""
    monkeypatch.setenv(uc._DISABLE_ENV, "1")
    with patch.object(uc.threading, "Thread") as mock_thread:
        uc.start_background_check("aethis-cli", "0.10.0")
    mock_thread.assert_not_called()


def test_print_banner_writes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    uc._print_banner("aethis-cli", "0.10.0", "0.11.0")
    captured = capsys.readouterr()
    assert "A new release of aethis-cli is available: 0.10.0 → 0.11.0" in captured.err
    assert "To upgrade, run: aethis update" in captured.err
    assert captured.out == ""


class _SyncThread:
    """Runs target synchronously on .start(); makes background_check deterministic."""

    def __init__(self, target=None, args=(), daemon=None, name=None) -> None:
        self._target = target
        self._args = args

    def start(self) -> None:
        self._target(*self._args)

    def join(self, timeout: float | None = None) -> None:
        pass


def _wire_sync(monkeypatch: pytest.MonkeyPatch) -> list:
    """Make start_background_check fully synchronous and capture atexit callbacks."""
    monkeypatch.delenv(uc._DISABLE_ENV, raising=False)
    monkeypatch.setattr(uc, "_should_skip", lambda: False)
    monkeypatch.setattr(uc.threading, "Thread", _SyncThread)
    callbacks: list = []
    monkeypatch.setattr(uc.atexit, "register", lambda fn: callbacks.append(fn))
    return callbacks


def test_start_background_check_prints_banner_when_newer(
    fake_config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Newer version on PyPI → banner appears on stderr when atexit fires."""
    callbacks = _wire_sync(monkeypatch)
    monkeypatch.setattr(uc, "_fetch_latest_pypi", lambda pkg: "0.11.0")

    uc.start_background_check("aethis-cli", "0.10.0")

    assert len(callbacks) == 1
    callbacks[0]()
    captured = capsys.readouterr()
    assert "A new release of aethis-cli is available: 0.10.0 → 0.11.0" in captured.err
    assert "To upgrade, run:" in captured.err


def test_start_background_check_no_banner_when_current(
    fake_config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    callbacks = _wire_sync(monkeypatch)
    monkeypatch.setattr(uc, "_fetch_latest_pypi", lambda pkg: "0.10.0")

    uc.start_background_check("aethis-cli", "0.10.0")

    callbacks[0]()
    captured = capsys.readouterr()
    assert captured.err == ""


def test_start_background_check_no_banner_on_network_failure(
    fake_config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    callbacks = _wire_sync(monkeypatch)
    monkeypatch.setattr(uc, "_fetch_latest_pypi", lambda pkg: None)

    uc.start_background_check("aethis-cli", "0.10.0")

    callbacks[0]()
    captured = capsys.readouterr()
    assert captured.err == ""
