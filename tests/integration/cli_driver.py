"""Drive the real ``aethis`` CLI as a subprocess against staging.

Shared by the happy-path and negative-path staging tests. The minted key is
passed via the global ``--api-key`` flag and is deliberately kept out of the
echoed command line so a secret never lands in test output or CI logs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_TIMEOUT = 90


def run_cli(
    args: list[str],
    base_url: str,
    api_key: str | None = None,
    json_output: bool = False,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["AETHIS_BASE_URL"] = base_url
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # ``--api-key`` and ``--output`` are options on the root callback, so they
    # must precede the subcommand name; only ``args`` follows it.
    globals_: list[str] = []
    if api_key is not None:
        globals_ += ["--api-key", api_key]
    if json_output:
        globals_ += ["--output", "json"]
    full_args = [*globals_, *args]
    result = subprocess.run(
        [sys.executable, "-m", "aethis_cli", *full_args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT,
    )
    # Echo the visible command from ``args`` only — never the key.
    sys.stdout.write(f"\n$ aethis {' '.join(args)}\n{result.stdout}{result.stderr}")
    return result
