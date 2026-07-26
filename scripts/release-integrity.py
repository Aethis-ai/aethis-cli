#!/usr/bin/env python3
"""Emit the release integrity tuple for a built distribution.

A published package is only trustworthy if the exact bytes a user installs
can be tied back to reviewed source. This prints that binding as one
machine-readable record:

    version + sdist sha256 + wheel sha256 + source commit + repository

## What the tuple is, and what it is not

It is an **attestation by the job that built the artefact**, not something a
third party can re-derive. `uv build` is not byte-reproducible here:

* the **wheel** IS byte-identical across builds of an identical tree, but only
  when `SOURCE_DATE_EPOCH` is set (otherwise embedded timestamps differ);
* the **sdist** is NOT, even with `SOURCE_DATE_EPOCH`: the archived content is
  identical, but the gzip header carries a build timestamp, so its `sha256`
  differs per build.

So a digest proves "these are the bytes that job produced and published"; it
does not let someone rebuild from the commit and expect the same hash. Two
builds of one commit legitimately disagree on the sdist digest. Measure it
rather than believing this note: `--verify-reproducible` builds twice and
reports which artefacts were stable.

The chain that IS load-bearing:

* **at build time**, the digests of the files in `dist/` are recorded against
  the commit they were built from, in a clean checkout (`--require-clean`);
* **at publication**, those exact digests are compared against the files the
  registry serves (`--verify-registry`) — same bytes, not merely the same
  version string;
* the source commit's link to reviewed code is carried by the protected
  branch and the workflow run, not by the digest.

Non-interactive by construction: no prompts, bounded network timeouts, exit
code 0 only when every requested check passed.

    uv build
    uv run python scripts/release-integrity.py
    uv run python scripts/release-integrity.py --verify-registry       # after publish
    uv run python scripts/release-integrity.py --verify-reproducible   # measure it
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parent.parent
PACKAGE = "aethis-cli"
PYPI_JSON = "https://pypi.org/pypi/aethis-cli/json"
NETWORK_TIMEOUT = 30


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(*args: str) -> Optional[str]:
    try:
        out = subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _declared_version() -> str:
    import tomllib

    with (REPO / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def _module_version() -> str:
    namespace: Dict[str, Any] = {}
    exec((REPO / "aethis_cli" / "_version.py").read_text(), namespace)  # noqa: S102
    return namespace["__version__"]


def _source_state() -> Dict[str, Any]:
    commit = _run("git", "rev-parse", "HEAD")
    dirty = _run("git", "status", "--porcelain")
    return {
        "repository": "https://github.com/Aethis-ai/aethis-cli",
        "commit": commit,
        "branch": _run("git", "rev-parse", "--abbrev-ref", "HEAD"),
        # A dirty tree means the artefact does not correspond to any reviewed
        # commit. Recorded rather than assumed away; `--require-clean` turns
        # it into a failure for release use.
        "working_tree_clean": dirty == "",
    }


def _distribution_files(dist: Path, version: str) -> Dict[str, Dict[str, Any]]:
    files: Dict[str, Dict[str, Any]] = {}
    for path in sorted(dist.glob("*")):
        if path.suffix not in (".gz", ".whl") and not path.name.endswith(".tar.gz"):
            continue
        if version not in path.name:
            continue
        kind = "wheel" if path.suffix == ".whl" else "sdist"
        files[kind] = {
            "filename": path.name,
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
    return files


def _registry_files(version: str) -> Dict[str, Dict[str, Any]]:
    try:
        with urllib.request.urlopen(PYPI_JSON, timeout=NETWORK_TIMEOUT) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit(f"could not read the registry index: {exc}")

    files: Dict[str, Dict[str, Any]] = {}
    for entry in data.get("releases", {}).get(version, []):
        kind = "wheel" if entry.get("packagetype") == "bdist_wheel" else "sdist"
        files[kind] = {
            "filename": entry.get("filename"),
            "sha256": entry.get("digests", {}).get("sha256"),
            "size_bytes": entry.get("size"),
            "url": entry.get("url"),
        }
    return files


def build_record(dist: Path, *, verify_registry: bool) -> Dict[str, Any]:
    declared = _declared_version()
    module = _module_version()

    record: Dict[str, Any] = {
        "package": PACKAGE,
        "version": declared,
        "version_sources_agree": declared == module,
        "source": _source_state(),
        "distribution": _distribution_files(dist, declared) if dist.is_dir() else {},
    }
    if not record["version_sources_agree"]:
        record["version_mismatch"] = {"pyproject.toml": declared, "aethis_cli/_version.py": module}

    record["reproducibility"] = {
        "source_date_epoch": os.environ.get("SOURCE_DATE_EPOCH"),
        "wheel_byte_reproducible": "with SOURCE_DATE_EPOCH set",
        "sdist_byte_reproducible": False,
        "note": (
            "The sdist's archived content is reproducible but its gzip header "
            "carries a build timestamp, so its sha256 differs per build. These "
            "digests attest what THIS job built and published; they are not "
            "re-derivable by rebuilding the commit. Measure with "
            "--verify-reproducible."
        ),
    }

    if verify_registry:
        registry = _registry_files(declared)
        record["registry"] = {"index": PYPI_JSON, "files": registry}
        matches = {}
        for kind, built in record["distribution"].items():
            published = registry.get(kind)
            matches[kind] = bool(published) and published.get("sha256") == built["sha256"]
        record["registry"]["matches_build"] = matches
    return record


def problems(record: Dict[str, Any], *, require_clean: bool, verify_registry: bool) -> list:
    found = []
    if not record["version_sources_agree"]:
        found.append(f"version mismatch: {record['version_mismatch']}")
    if record["source"]["commit"] is None:
        found.append("source commit unavailable (not a git checkout?)")
    if require_clean and not record["source"]["working_tree_clean"]:
        found.append("working tree is dirty: the artefact matches no reviewed commit")
    if not record["distribution"]:
        found.append("no distribution files found: run `uv build` first")
    else:
        for kind in ("sdist", "wheel"):
            if kind not in record["distribution"]:
                found.append(f"no {kind} in dist/")
    if verify_registry:
        for kind, ok in record.get("registry", {}).get("matches_build", {}).items():
            if not ok:
                found.append(f"{kind} on the registry does not match the local build")
    return found


def measure_reproducibility() -> Dict[str, Any]:
    """Build twice into scratch directories and report what was stable.

    The claim in this module's docstring is only worth what a measurement
    says, so this makes it checkable instead of asserted.
    """
    digests: Dict[str, list] = {}
    for _ in range(2):
        with tempfile.TemporaryDirectory(prefix="aethis-repro-") as tmp:
            built = subprocess.run(
                ["uv", "build", "--quiet", "-o", tmp],
                cwd=REPO,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            if built.returncode != 0:
                return {"error": built.stderr.strip()[:500]}
            for path in sorted(Path(tmp).glob("*")):
                if path.suffix == ".whl":
                    kind = "wheel"
                elif path.name.endswith(".tar.gz"):
                    kind = "sdist"
                else:
                    continue
                digests.setdefault(kind, []).append(_sha256(path))

    return {
        "source_date_epoch": os.environ.get("SOURCE_DATE_EPOCH"),
        "stable": {kind: len(set(values)) == 1 for kind, values in digests.items()},
        "digests": digests,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dist", default=str(REPO / "dist"), help="directory holding the built distribution")
    parser.add_argument("--require-clean", action="store_true", help="fail if the working tree is dirty")
    parser.add_argument(
        "--verify-registry",
        action="store_true",
        help="compare the local build against the files the registry serves",
    )
    parser.add_argument(
        "--verify-reproducible",
        action="store_true",
        help="build twice and report which artefacts were byte-identical",
    )
    parser.add_argument("--output", help="also write the record to this path")
    args = parser.parse_args()

    record = build_record(Path(args.dist), verify_registry=args.verify_registry)
    if args.verify_reproducible:
        record["reproducibility"]["measured"] = measure_reproducibility()
    failures = problems(record, require_clean=args.require_clean, verify_registry=args.verify_registry)
    record["ok"] = not failures
    if failures:
        record["problems"] = failures

    text = json.dumps(record, indent=2, sort_keys=True)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")

    for failure in failures:
        print(f"integrity: {failure}", file=sys.stderr)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
