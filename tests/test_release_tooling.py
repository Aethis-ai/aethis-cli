"""Release tooling: the integrity tuple and the hermetic first-user check.

These two scripts are the evidence a release candidate is assembled from, so
they get the same treatment as shipped code. The expensive parts (a real
install, a registry read) are exercised by CI and by the release run; what is
tested here is the logic that decides pass from fail -- because a check that
cannot fail proves nothing.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent.parent / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


integrity = _load("release_integrity", "release-integrity.py")
hermetic = _load("hermetic_install_check", "hermetic-install-check.py")


# ---------------------------------------------------------------------------
# Integrity tuple
# ---------------------------------------------------------------------------


def test_version_sources_agree():
    """`pyproject.toml` and `_version.py` are the same number, always.

    They are read by different consumers -- the build backend and the running
    CLI -- so a drift between them means the artefact reports a version it was
    not built as.
    """
    assert integrity._declared_version() == integrity._module_version()


def test_integrity_record_binds_artefact_to_source(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    version = integrity._declared_version()
    (dist / f"aethis_cli-{version}.tar.gz").write_bytes(b"sdist bytes")
    (dist / f"aethis_cli-{version}-py3-none-any.whl").write_bytes(b"wheel bytes")

    record = integrity.build_record(dist, verify_registry=False)
    assert record["version"] == version
    assert set(record["distribution"]) == {"sdist", "wheel"}
    assert len(record["distribution"]["wheel"]["sha256"]) == 64
    assert record["source"]["repository"].endswith("/aethis-cli")
    assert record["source"]["commit"]
    assert integrity.problems(record, require_clean=False, verify_registry=False) == []


def test_integrity_fails_when_nothing_was_built(tmp_path):
    record = integrity.build_record(tmp_path / "absent", verify_registry=False)
    found = integrity.problems(record, require_clean=False, verify_registry=False)
    assert any("uv build" in problem for problem in found)


def test_integrity_fails_on_a_registry_digest_mismatch(tmp_path):
    record = {
        "version_sources_agree": True,
        "source": {"commit": "abc123", "working_tree_clean": True},
        "distribution": {
            "sdist": {"sha256": "a" * 64},
            "wheel": {"sha256": "b" * 64},
        },
        "registry": {"matches_build": {"sdist": True, "wheel": False}},
    }
    found = integrity.problems(record, require_clean=False, verify_registry=True)
    assert any("wheel on the registry does not match" in problem for problem in found)


def test_integrity_can_require_a_clean_tree():
    record = {
        "version_sources_agree": True,
        "source": {"commit": "abc123", "working_tree_clean": False},
        "distribution": {"sdist": {}, "wheel": {}},
    }
    found = integrity.problems(record, require_clean=True, verify_registry=False)
    assert any("dirty" in problem for problem in found)
    assert integrity.problems(record, require_clean=False, verify_registry=False) == []


# ---------------------------------------------------------------------------
# Hermetic environment
# ---------------------------------------------------------------------------


def test_hermetic_env_strips_every_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHIS_API_KEY", "ak_live_should_not_survive")
    monkeypatch.setenv("AETHIS_BASE_URL", "https://staging.api.aethis.ai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-survive")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-survive")
    monkeypatch.setenv("PYTHONPATH", "/somewhere/contaminated")
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/else")

    env = hermetic._hermetic_env(tmp_path)

    assert "AETHIS_API_KEY" not in env
    assert "AETHIS_BASE_URL" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "PYTHONPATH" not in env
    assert "VIRTUAL_ENV" not in env


def test_hermetic_env_redirects_all_user_state_into_the_sandbox(tmp_path):
    env = hermetic._hermetic_env(tmp_path)
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "UV_CACHE_DIR"):
        assert env[key].startswith(str(tmp_path)), key
        assert Path(env[key]).is_dir()
    # An empty cache is the point: a first-run install must not be able to
    # resolve anything a previous run left behind.
    assert not any(Path(env["UV_CACHE_DIR"]).iterdir())
    # Nothing may prompt.
    assert env["AETHIS_NONINTERACTIVE"] == "1"
    assert env["CI"] == "1"


def test_poison_puts_a_stale_cli_ahead_of_the_fresh_one(tmp_path):
    env = hermetic._hermetic_env(tmp_path)
    original_path = env["PATH"]
    hermetic._poison(tmp_path, env)

    shim = tmp_path / "poison" / "bin" / "aethis"
    assert shim.is_file()
    assert env["PATH"].startswith(str(shim.parent))
    assert env["PATH"].endswith(original_path)
    assert env["PYTHONPATH"] == str(tmp_path / "poison" / "site")
    assert (tmp_path / "poison" / "site" / "aethis_cli" / "_version.py").is_file()


def test_install_spec_selects_one_source_only(tmp_path):
    version = integrity._declared_version()
    assert hermetic._install_spec("registry", version, tmp_path) == f"aethis-cli=={version}"

    with pytest.raises(hermetic.CheckFailed):
        hermetic._install_spec("dist", version, tmp_path)

    wheel = tmp_path / f"aethis_cli-{version}-py3-none-any.whl"
    wheel.write_bytes(b"")
    assert hermetic._install_spec("dist", version, tmp_path) == str(wheel)


@pytest.mark.parametrize(
    "record,expected_ok",
    [
        ({"passed": False, "failures": ["stale shim"]}, True),
        ({"passed": True, "failures": []}, False),
    ],
)
def test_poisoned_control_inverts_the_verdict(record, expected_ok):
    """A poisoned run that passes means the assertions are inert -- which is a
    failure of the check, not a success of the install."""
    detected = not record["passed"]
    assert detected is expected_ok


def test_scripts_emit_machine_readable_records(tmp_path):
    """P10 consumes these as data, not as terminal prose."""
    dist = tmp_path / "dist"
    dist.mkdir()
    version = integrity._declared_version()
    (dist / f"aethis_cli-{version}.tar.gz").write_bytes(b"x")
    (dist / f"aethis_cli-{version}-py3-none-any.whl").write_bytes(b"y")
    record = integrity.build_record(dist, verify_registry=False)
    assert json.loads(json.dumps(record)) == record
