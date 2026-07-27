"""Load, validate and resolve ``--source-targets`` for ``aethis publish``.

A targets file maps each opaque citation key declared by the ruleset's
criteria (``source_refs``) to the document that key cites. Each entry names
**exactly one** target:

* ``url`` — a public HTTPS document. The engine fetches it SSRF-safe at
  publish time, verifies the verbatim quote against the fetched bytes, and
  retains a snapshot of them. Emitted as a schema **v1** reference.
* ``file`` — a local file, uploaded to the project as a source and cited by
  its ``source_id``. The engine resolves it from the retained bytes with zero
  network calls. Emitted as a schema **v2** artefact reference, whose download
  URL is authenticated (never anonymous).

Everything this module can check without the network is checked *before* the
first API call: exactly-one-target, required fields, a readable file, an
HTTPS URL. A malformed targets file must never cost a round trip, and must
never leave a half-published ruleset behind.

Format (YAML or JSON)::

    "BNA1981#Schedule1/P1.1":
      url: https://www.legislation.gov.uk/ukpga/1981/61/schedule/1
      title: British Nationality Act 1981, Schedule 1
      authority: UK Government
      licence: OGL-UK-3.0
      locator: Paragraph 1(1)(a)          # optional
      quote:
        exact: "is of full age and capacity"

    "HO-GUIDE#4.2":
      file: ./corpus/naturalisation-guidance.pdf
      title: Naturalisation booklet AN
      authority: Home Office
      licence: OGL-UK-3.0
      quote:
        exact: "You must have been resident in the UK"
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

#: Every member an entry may carry. An unknown key is a typo, and a silently
#: ignored typo is how a citation ends up missing its locator on a published,
#: immutable ruleset — so it fails loudly instead.
ALLOWED_KEYS = frozenset(
    {
        "url",
        "file",
        "title",
        "authority",
        "licence",
        "quote",
        "locator",
        "source_version",
        "source_date",
        "expected_digest",
    }
)

#: Members every entry must supply, whichever target kind it names.
REQUIRED_KEYS = ("title", "authority", "licence")

ALLOWED_QUOTE_KEYS = frozenset({"exact", "prefix", "suffix"})


class SourceTargetsError(ValueError):
    """The targets file is unusable. Carries every problem found, not just
    the first — an author fixing one typo per round trip is the failure mode
    this avoids."""

    def __init__(self, problems: List[str], *, path: Optional[Path] = None) -> None:
        self.problems = problems
        self.path = path
        where = f" in {path}" if path else ""
        super().__init__(f"Invalid source targets{where}:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass(frozen=True)
class SourceTarget:
    """One validated citation target, before any API call."""

    key: str
    title: str
    authority: str
    licence: str
    quote: Dict[str, str]
    url: Optional[str] = None
    file: Optional[Path] = None
    locator: Optional[str] = None
    source_version: Optional[str] = None
    source_date: Optional[str] = None
    expected_digest: Optional[str] = None

    @property
    def is_artefact(self) -> bool:
        """True when this target cites an uploaded file (schema v2)."""
        return self.file is not None

    def wire(self, artefact_source_id: Optional[str] = None) -> Dict[str, Any]:
        """The engine-side ``SourceTargetIn`` body for this target.

        ``artefact_source_id`` is required for file targets and is the
        ``source_id`` the upload (or the reused existing source) returned.
        """
        body: Dict[str, Any] = {
            "title": self.title,
            "authority": self.authority,
            "licence": self.licence,
            "quote": dict(self.quote),
        }
        if self.is_artefact:
            if not artefact_source_id:
                raise ValueError(f"file target {self.key!r} has no resolved artefact_source_id")
            body["artefact_source_id"] = artefact_source_id
        else:
            body["url"] = self.url
        for optional in ("locator", "source_version", "source_date", "expected_digest"):
            value = getattr(self, optional)
            if value is not None:
                body[optional] = value
        return body


@dataclass(frozen=True)
class ResolvedTarget:
    """What one target resolved to, for rendering. ``reused`` is True when an
    identical file was already uploaded to the project and no second copy was
    created."""

    key: str
    kind: str  # "url" | "artefact"
    detail: str  # the URL, or the artefact source_id
    reused: bool = False


# ---------------------------------------------------------------------------
# Loading + local validation
# ---------------------------------------------------------------------------


def _reject_duplicate_pairs(pairs: List[Tuple[Any, Any]]) -> Dict[Any, Any]:
    """Build a mapping, refusing duplicate keys.

    Both JSON and YAML silently let the last definition of a repeated key win.
    For a citation manifest that is a trap: an author who defines the same key
    twice loses one of the two documents with no signal, and the published,
    immutable ruleset cites whichever happened to come second.
    """
    seen: Dict[Any, Any] = {}
    duplicates: List[str] = []
    for key, value in pairs:
        if key in seen:
            duplicates.append(str(key))
        seen[key] = value
    if duplicates:
        raise SourceTargetsError(
            [
                f"duplicate citation key {key!r} — each key may be defined once "
                "(a repeated key silently discards the earlier definition)"
                for key in sorted(set(duplicates))
            ]
        )
    return seen


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys."""


def _strict_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> Dict[Any, Any]:
    loader.flatten_mapping(node)
    return _reject_duplicate_pairs(loader.construct_pairs(node, deep=True))


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_mapping)


def _parse(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SourceTargetsError([f"cannot read the targets file: {exc}"], path=path) from exc
    if path.suffix.lower() == ".json":
        try:
            return json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
        except json.JSONDecodeError as exc:
            raise SourceTargetsError([f"invalid JSON: {exc}"], path=path) from exc
        except SourceTargetsError as exc:
            raise SourceTargetsError(exc.problems, path=path) from exc
    try:
        return yaml.load(text, Loader=_StrictLoader)  # noqa: S506 - _StrictLoader derives from SafeLoader
    except yaml.YAMLError as exc:
        raise SourceTargetsError([f"invalid YAML: {exc}"], path=path) from exc
    except SourceTargetsError as exc:
        raise SourceTargetsError(exc.problems, path=path) from exc


def _validate_quote(key: str, raw: Any, problems: List[str]) -> Dict[str, str]:
    if not isinstance(raw, Mapping):
        problems.append(f"{key}: 'quote' must be a mapping with an 'exact' member, e.g. quote: {{exact: \"...\"}}")
        return {}
    unknown = sorted(set(raw) - ALLOWED_QUOTE_KEYS)
    if unknown:
        problems.append(f"{key}: unknown quote member(s) {', '.join(unknown)} (allowed: exact, prefix, suffix)")
    quote: Dict[str, str] = {}
    for member in ("exact", "prefix", "suffix"):
        value = raw.get(member)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{key}: quote.{member} must be a non-empty string")
            continue
        quote[member] = value
    if "exact" not in quote and "exact" not in raw:
        problems.append(
            f"{key}: quote.exact is required — the verbatim text this citation quotes "
            "(never a summary or paraphrase; the engine checks it occurs in the source)"
        )
    return quote


def _validate_entry(key: str, raw: Any, base_dir: Path, problems: List[str]) -> Optional[SourceTarget]:
    if not isinstance(raw, Mapping):
        problems.append(f"{key}: entry must be a mapping of fields, got {type(raw).__name__}")
        return None

    unknown = sorted(set(raw) - ALLOWED_KEYS)
    if unknown:
        problems.append(f"{key}: unknown field(s) {', '.join(unknown)} (allowed: {', '.join(sorted(ALLOWED_KEYS))})")

    url = raw.get("url")
    file_raw = raw.get("file")
    if bool(url) == bool(file_raw):
        problems.append(
            f"{key}: supply exactly one of 'url' (a public HTTPS document) or "
            "'file' (a local file uploaded and cited as a retained artefact) — "
            f"this entry has {'both' if url else 'neither'}"
        )

    if url is not None:
        if not isinstance(url, str) or not url.strip():
            problems.append(f"{key}: 'url' must be a non-empty string")
            url = None
        elif not url.startswith("https://"):
            problems.append(
                f"{key}: 'url' must be an absolute https:// URL (got {url!r}) — the engine fetches HTTPS only"
            )

    resolved_file: Optional[Path] = None
    if file_raw is not None:
        if not isinstance(file_raw, str) or not file_raw.strip():
            problems.append(f"{key}: 'file' must be a non-empty path string")
        else:
            candidate = Path(file_raw).expanduser()
            if not candidate.is_absolute():
                candidate = (base_dir / candidate).resolve()
            if not candidate.exists():
                problems.append(f"{key}: file not found: {candidate}")
            elif candidate.is_dir():
                problems.append(f"{key}: 'file' is a directory, not a file: {candidate}")
            else:
                try:
                    with candidate.open("rb"):
                        pass
                except OSError as exc:
                    problems.append(f"{key}: file is not readable: {candidate} ({exc})")
                else:
                    resolved_file = candidate

    values: Dict[str, str] = {}
    for required in REQUIRED_KEYS:
        value = raw.get(required)
        if not isinstance(value, str) or not value.strip():
            problems.append(
                f"{key}: '{required}' is required and must be a non-empty string"
                + (" — an unlicensed citation is rejected by the engine" if required == "licence" else "")
            )
        else:
            values[required] = value

    quote = _validate_quote(key, raw.get("quote"), problems)

    for optional in ("locator", "source_version", "source_date", "expected_digest"):
        value = raw.get(optional)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            problems.append(f"{key}: '{optional}' must be a non-empty string when present")

    if len(values) != len(REQUIRED_KEYS) or "exact" not in quote:
        return None
    if (resolved_file is None) == (url is None):
        # Exactly-one already reported above; nothing coherent to build.
        return None

    return SourceTarget(
        key=key,
        title=values["title"],
        authority=values["authority"],
        licence=values["licence"],
        quote=quote,
        url=url if resolved_file is None else None,
        file=resolved_file,
        locator=raw.get("locator"),
        source_version=raw.get("source_version"),
        source_date=raw.get("source_date"),
        expected_digest=raw.get("expected_digest"),
    )


def load_source_targets(path: Path) -> List[SourceTarget]:
    """Parse and fully validate a targets file. Raises ``SourceTargetsError``
    listing every problem found — no API call is worth making until this
    passes."""
    data = _parse(path)
    if data is None:
        raise SourceTargetsError(["the file is empty — expected a mapping of citation key to target"], path=path)
    if not isinstance(data, Mapping):
        raise SourceTargetsError(
            [f"top level must be a mapping of citation key to target, got {type(data).__name__}"],
            path=path,
        )
    if not data:
        raise SourceTargetsError(["no citation keys defined"], path=path)

    problems: List[str] = []
    targets: List[SourceTarget] = []
    base_dir = path.parent.resolve()
    for key, raw in data.items():
        if not isinstance(key, str) or not key.strip():
            problems.append(f"citation keys must be non-empty strings (got {key!r})")
            continue
        target = _validate_entry(key, raw, base_dir, problems)
        if target is not None:
            targets.append(target)

    if problems:
        raise SourceTargetsError(problems, path=path)
    return targets


# ---------------------------------------------------------------------------
# Resolution (uploads + dedupe)
# ---------------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_sources_by_digest(client: Any, project_id: str) -> Dict[str, str]:
    """``raw_sha256 -> source_id`` for the project's existing sources.

    The engine never dedupes on upload (it only reports ``possible_
    duplicates`` and always inserts), so this client-side check is the only
    thing standing between a re-run of the same publish and a project full of
    identical sources. First match wins so the mapping is stable across runs.
    """
    response = client.list_sources(project_id) or {}
    by_digest: Dict[str, str] = {}
    for source in response.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        digest = source.get("raw_sha256")
        source_id = source.get("source_id")
        if digest and source_id:
            by_digest.setdefault(str(digest), str(source_id))
    return by_digest


def resolve_source_targets(
    client: Any,
    project_id: str,
    targets: List[SourceTarget],
) -> Tuple[Dict[str, Dict[str, Any]], List[ResolvedTarget]]:
    """Turn validated targets into the publish body's ``source_targets`` map.

    URL targets pass straight through. File targets are matched by sha256
    against the project's existing sources and reused when the bytes are
    already there; otherwise they are uploaded and cited by the returned
    ``source_id``. Two entries naming byte-identical files upload once.
    """
    wire: Dict[str, Dict[str, Any]] = {}
    resolutions: List[ResolvedTarget] = []

    by_digest: Optional[Dict[str, str]] = None
    for target in targets:
        if not target.is_artefact:
            wire[target.key] = target.wire()
            resolutions.append(ResolvedTarget(target.key, "url", target.url or ""))
            continue

        if by_digest is None:
            by_digest = _existing_sources_by_digest(client, project_id)

        assert target.file is not None
        digest = file_sha256(target.file)
        source_id = by_digest.get(digest)
        reused = source_id is not None
        if source_id is None:
            uploaded = client.upload_sources(project_id, [target.file]) or {}
            sources = uploaded.get("sources") or []
            if not sources or not isinstance(sources[0], Mapping) or not sources[0].get("source_id"):
                raise SourceTargetsError(
                    [f"{target.key}: upload of {target.file} returned no source_id — cannot cite it as an artefact"]
                )
            source_id = str(sources[0]["source_id"])
            # Record it so a second entry naming byte-identical content in the
            # same run cites the same upload instead of making another.
            by_digest[digest] = source_id

        wire[target.key] = target.wire(artefact_source_id=source_id)
        resolutions.append(ResolvedTarget(target.key, "artefact", source_id, reused=reused))

    return wire, resolutions


__all__ = [
    "ALLOWED_KEYS",
    "REQUIRED_KEYS",
    "ResolvedTarget",
    "SourceTarget",
    "SourceTargetsError",
    "file_sha256",
    "load_source_targets",
    "resolve_source_targets",
]
