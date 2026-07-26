#!/usr/bin/env python3
"""Emit the release integrity tuple for a built distribution.

A published package is only trustworthy if the exact bytes a user installs
can be tied back to reviewed source. This prints that binding as one
machine-readable record:

    version + sdist sha256 + wheel sha256 + source commit + repository

The same tuple is produced at three points and must agree at all three:

* **at build time**, from `dist/` in a clean checkout (this script's default);
* **at release time**, in CI, where the source commit is the tag's commit on
  protected `main` and the workflow run is the evidence of green checks;
* **after publication**, against the files PyPI actually serves
  (`--verify-registry`), which is what proves the registry holds the artefact
  that was built and not something else.

Non-interactive by construction: no prompts, bounded network timeouts, exit
code 0 only when every requested check passed.

    uv build
    uv run python scripts/release-integrity.py
    uv run python scripts/release-integrity.py --verify-registry   # after publish
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dist", default=str(REPO / "dist"), help="directory holding the built distribution")
    parser.add_argument("--require-clean", action="store_true", help="fail if the working tree is dirty")
    parser.add_argument(
        "--verify-registry",
        action="store_true",
        help="compare the local build against the files the registry serves",
    )
    parser.add_argument("--output", help="also write the record to this path")
    args = parser.parse_args()

    record = build_record(Path(args.dist), verify_registry=args.verify_registry)
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
