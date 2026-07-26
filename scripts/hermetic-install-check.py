#!/usr/bin/env python3
"""Prove the CLI works for someone who has never run it.

Every "works on my machine" install is contaminated: a cached wheel, a key in
the environment, a credentials file from last month, an older `aethis` earlier
on PATH. This check removes all of that and then asserts the first-run
behaviour a new developer actually gets.

What it controls for:

* a **temporary HOME, XDG config/cache/data and uv cache** -- no state from
  the invoking user reaches the installed CLI, and nothing the check does
  outlives it;
* **no Aethis or model-provider credentials** in the environment, so anything
  that only works because a key happened to be exported fails here;
* an **empty-cache install** from exactly one source: the built distribution
  (pre-publication rehearsal) or the registry (post-publication proof);
* the **runtime, OS and architecture** it ran on, recorded in the result so a
  matrix run says what it actually covered;
* a **poisoned-cache negative control** (`--poison`): the same run against a
  deliberately contaminated environment, which must FAIL. A check that cannot
  fail is not evidence, and this is how we know the assertions bite.

Nothing here needs an API key or a network call to the Aethis API: the
assertions are about the installed artefact, not the service.

    uv build
    uv run python scripts/hermetic-install-check.py --source dist
    uv run python scripts/hermetic-install-check.py --source dist --poison
    uv run python scripts/hermetic-install-check.py --source registry   # after publish
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
INSTALL_TIMEOUT = 600
RUN_TIMEOUT = 120

#: Credentials that must not be visible to a first-run install. Anything
#: matching one of these prefixes is stripped from the child environment.
SCRUBBED_PREFIXES = ("AETHIS_",)
SCRUBBED_NAMES = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "COHERE_API_KEY",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONHOME",
)


class CheckFailed(Exception):
    """A hermetic assertion did not hold."""


def _declared_version() -> str:
    import tomllib

    with (REPO / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def _hermetic_env(root: Path) -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(SCRUBBED_PREFIXES) and k not in SCRUBBED_NAMES}
    home = root / "home"
    for path in (home, root / "config", root / "cache", root / "data", root / "uv-cache"):
        path.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_DATA_HOME": str(root / "data"),
            "UV_CACHE_DIR": str(root / "uv-cache"),
            # No update banner, no telemetry noise, nothing that could prompt.
            "AETHIS_NONINTERACTIVE": "1",
            "CI": "1",
            "NO_COLOR": "1",
            "COLUMNS": "200",
        }
    )
    return env


def _run(argv: List[str], env: Dict[str, str], timeout: int = RUN_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout, check=False)


def _install_spec(source: str, version: str, dist: Path) -> str:
    if source == "registry":
        return f"aethis-cli=={version}"
    candidates = sorted(dist.glob(f"aethis_cli-{version}-*.whl"))
    if not candidates:
        raise CheckFailed(f"no wheel for {version} in {dist} -- run `uv build` first")
    return str(candidates[0])


def _poison(root: Path, env: Dict[str, str]) -> None:
    """Contaminate the environment the way a real machine gets contaminated.

    A stale `aethis` shim earlier on PATH and a stale importable package: the
    two ways a user ends up running something other than what they just
    installed. The check must notice.
    """
    poison_bin = root / "poison" / "bin"
    poison_bin.mkdir(parents=True, exist_ok=True)
    shim = poison_bin / "aethis"
    shim.write_text('#!/bin/sh\necho "aethis 0.0.1-stale"\n')
    shim.chmod(0o755)

    poison_pkg = root / "poison" / "site" / "aethis_cli"
    poison_pkg.mkdir(parents=True, exist_ok=True)
    (poison_pkg / "__init__.py").write_text("")
    (poison_pkg / "_version.py").write_text('__version__ = "0.0.1-stale"\n')

    env["PATH"] = f"{poison_bin}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(root / "poison" / "site")


#: Assertions the poisoned control MUST trip. If a poisoned run fails for
#: any other reason -- a missing wheel, a network error, uv absent -- the
#: control proves nothing: it never got as far as the checks it is meant to
#: be exercising.
POISON_MUST_DETECT = ("stale-binary", "version-mismatch")


def _assert(condition: bool, message: str, failures: List[str], tag: str = "") -> None:
    if not condition:
        failures.append(f"[{tag}] {message}" if tag else message)


def _tags(failures: List[str]) -> List[str]:
    return [f.split("]")[0].lstrip("[") for f in failures if f.startswith("[")]


def run_check(
    *,
    source: str,
    version: str,
    dist: Path,
    python: Optional[str],
    poison: bool,
) -> Dict[str, Any]:
    started = time.monotonic()
    root = Path(tempfile.mkdtemp(prefix="aethis-hermetic-"))
    failures: List[str] = []
    record: Dict[str, Any] = {
        "package": "aethis-cli",
        "expected_version": version,
        "source": source,
        "poisoned_control": poison,
        "runtime": {
            "python": python or f"{sys.version_info.major}.{sys.version_info.minor}",
            "os": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
        },
        "sandbox": {"root": str(root), "isolated_home": True},
    }

    try:
        env = _hermetic_env(root)
        venv = root / "venv"

        uv = shutil.which("uv")
        if uv is None:
            raise CheckFailed("uv is not on PATH; this project installs with uv only")

        venv_argv = [uv, "venv", str(venv)]
        if python:
            venv_argv += ["--python", python]
        created = _run(venv_argv, env, timeout=INSTALL_TIMEOUT)
        if created.returncode != 0:
            raise CheckFailed(f"could not create the sandbox venv: {created.stderr.strip()}")

        spec = _install_spec(source, version, dist)
        record["install_spec"] = spec
        install_started = time.monotonic()
        installed = _run(
            [uv, "pip", "install", "--no-cache", "--python", str(venv / "bin" / "python"), spec],
            env,
            timeout=INSTALL_TIMEOUT,
        )
        record["install_seconds"] = round(time.monotonic() - install_started, 2)
        if installed.returncode != 0:
            raise CheckFailed(f"install failed: {installed.stderr.strip()[:2000]}")

        # The console script the user would actually invoke, found the way
        # their shell would find it. In the control run the stale shim is
        # deliberately ahead of the fresh install, which is exactly the
        # situation the check has to notice.
        env["PATH"] = f"{venv / 'bin'}{os.pathsep}{env.get('PATH', '')}"
        if poison:
            _poison(root, env)

        resolved = _run(["sh", "-c", "command -v aethis"], env)
        binary = resolved.stdout.strip()
        record["resolved_binary"] = binary
        _assert(
            bool(binary) and binary.startswith(str(venv)),
            f"the `aethis` on PATH is not the one just installed: {binary or '<not found>'}",
            failures,
            "stale-binary",
        )

        version_out = _run(["aethis", "--version"], env)
        record["reported_version"] = version_out.stdout.strip()
        _assert(
            version_out.returncode == 0 and version_out.stdout.strip() == f"aethis {version}",
            f"`aethis --version` reported {version_out.stdout.strip()!r}, expected 'aethis {version}'",
            failures,
            "version-mismatch",
        )

        help_out = _run(["aethis", "--help"], env)
        help_text = " ".join(help_out.stdout.split())
        _assert(help_out.returncode == 0, "`aethis --help` did not exit cleanly", failures, "help-exit")
        _assert(
            "no API key" in help_text,
            "root help does not state that evaluation needs no API key",
            failures,
            "help-no-key",
        )
        _assert(
            "invite-only" in help_text,
            "root help does not state that authoring is invite-only",
            failures,
            "help-invite-only",
        )

        decide_help = _run(["aethis", "decide", "--help"], env)
        decide_text = " ".join(decide_help.stdout.split())
        _assert(
            "Exit codes" in decide_text,
            "`aethis decide --help` does not document its exit codes",
            failures,
            "help-exit-codes",
        )

        # Nothing the CLI does on a first run may need, or invent, a credential.
        leaked = _run(
            [
                "sh",
                "-c",
                "env | grep -E '^(AETHIS_|ANTHROPIC_API_KEY|OPENAI_API_KEY)' || true",
            ],
            env,
        )
        visible = [line for line in leaked.stdout.splitlines() if not line.startswith("AETHIS_NONINTERACTIVE")]
        _assert(not visible, f"credential-shaped variables reached the sandbox: {visible}", failures, "leaked-env")

        credentials = Path(env["XDG_CONFIG_HOME"]) / "aethis" / "credentials"
        legacy = Path(env["HOME"]) / ".config" / "aethis" / "credentials"
        _assert(
            not credentials.exists() and not legacy.exists(),
            "a credentials file was created by a read-only first run",
            failures,
            "stray-credentials",
        )

        record["duration_seconds"] = round(time.monotonic() - started, 2)
    except CheckFailed as exc:
        failures.append(str(exc))
        record["duration_seconds"] = round(time.monotonic() - started, 2)
    except subprocess.TimeoutExpired as exc:
        failures.append(f"timed out after {exc.timeout}s: {exc.cmd}")
        record["duration_seconds"] = round(time.monotonic() - started, 2)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    record["failures"] = failures
    record["passed"] = not failures
    return record


def _detection_tags(failures: List[str]) -> List[str]:
    """Which named assertions actually tripped."""
    return _tags(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=("dist", "registry"), default="dist")
    parser.add_argument("--dist", default=str(REPO / "dist"))
    parser.add_argument("--version", default=None, help="defaults to the version declared in pyproject.toml")
    parser.add_argument("--python", default=None, help="Python version for the sandbox venv, e.g. 3.11")
    parser.add_argument(
        "--poison",
        action="store_true",
        help="negative control: contaminate the sandbox; the run MUST fail",
    )
    parser.add_argument("--output", help="also write the result record to this path")
    args = parser.parse_args()

    version = args.version or _declared_version()
    record = run_check(
        source=args.source,
        version=version,
        dist=Path(args.dist),
        python=args.python,
        poison=args.poison,
    )

    if args.poison:
        # A control is only evidence if it reached the checks and tripped the
        # RIGHT ones. The first version treated any failure as success, then
        # erased the failure list -- so `rm -rf dist && --poison` "passed" in
        # 0.25s having installed nothing, and the record could not say what
        # had been detected.
        detected = _detection_tags(record["failures"])
        missing = [tag for tag in POISON_MUST_DETECT if tag not in detected]
        setup_failures = [f for f in record["failures"] if not f.startswith("[")]

        record["control"] = {
            "expected_detections": list(POISON_MUST_DETECT),
            "detected": detected,
            "missing_detections": missing,
            "setup_failures": setup_failures,
            # Kept verbatim: the evidence IS what was detected.
            "observed_failures": record["failures"],
        }
        record["passed"] = not missing and not setup_failures
        record["failures"] = []
        if setup_failures:
            record["failures"].append(
                "poisoned control never reached the checks (setup failed): " + "; ".join(setup_failures)
            )
        if missing:
            record["failures"].append(
                "poisoned control did not trip: " + ", ".join(missing) + " -- those assertions do not bite"
            )

    text = json.dumps(record, indent=2, sort_keys=True)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    for failure in record["failures"]:
        print(f"hermetic: {failure}", file=sys.stderr)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
