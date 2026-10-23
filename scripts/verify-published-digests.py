#!/usr/bin/env python3
"""Check the registry serves the files this release built, byte for byte.

Same version string is not the same artefact: compare the sha256 the registry
serves against the digests recorded when the bytes were built.

The registry's index is eventually consistent, so right after an upload a
file can be absent for a short while. Absence is therefore retried within a
bound; a digest MISMATCH is never retried, because a different file is not
going to turn into the right one.

    python3 scripts/verify-published-digests.py --version 0.53.0 \
        --evidence evidence/release-integrity.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable

URL = "https://pypi.org/pypi/aethis-cli/{version}/json"
REQUEST_TIMEOUT = 30


def fetch_files(version: str) -> list[dict]:
    """Files the registry lists for `version`; empty until the index catches up."""
    try:
        with urllib.request.urlopen(URL.format(version=version), timeout=REQUEST_TIMEOUT) as fh:
            return json.load(fh).get("urls", [])
    except urllib.error.HTTPError as err:
        if err.code == 404 or err.code == 429 or err.code >= 500:
            return []  # not indexed yet, or a transient registry error: retry
        raise
    except (urllib.error.URLError, TimeoutError):
        return []  # transient network error: retry within the bound


def check(built: dict, published: list[dict]) -> tuple[list[str], list[str]]:
    """Return (missing, mismatched) messages for one registry reading."""
    by_kind = {("wheel" if f["packagetype"] == "bdist_wheel" else "sdist"): f for f in published}
    missing: list[str] = []
    mismatched: list[str] = []
    for kind, local in built.items():
        remote = by_kind.get(kind)
        if remote is None:
            missing.append(f"{kind} missing from the registry")
        elif remote["digests"]["sha256"] != local["sha256"]:
            mismatched.append(
                f"{kind} digest mismatch: built {local['sha256']}, registry {remote['digests']['sha256']}"
            )
        else:
            print(f"{kind}: {remote['filename']} sha256={local['sha256']} matches the build")
    return missing, mismatched


def verify(
    version: str,
    built: dict,
    *,
    fetch: Callable[[str], list[dict]] = fetch_files,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 12,
    max_delay: float = 40.0,
) -> list[str]:
    """Failures after polling; empty means every built file matches."""
    if not built:
        return ["no built distribution files in the evidence; nothing to verify"]
    delay = 5.0
    failures: list[str] = []
    for attempt in range(1, attempts + 1):
        missing, mismatched = check(built, fetch(version))
        if mismatched:
            return mismatched  # hard failure, no retry
        if not missing:
            return []
        failures = missing
        if attempt < attempts:
            print(f"attempt {attempt}/{attempts}: {'; '.join(missing)}; retrying in {delay:.0f}s")
            sleep(delay)
            delay = min(delay * 2, max_delay)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    with open(args.evidence) as fh:
        built = json.load(fh)["distribution"]
    failures = verify(args.version, built)
    if failures:
        sys.exit("; ".join(failures))
    return 0


if __name__ == "__main__":
    sys.exit(main())
