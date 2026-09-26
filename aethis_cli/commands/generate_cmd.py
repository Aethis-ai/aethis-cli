"""aethis generate — upload sources + guidance, trigger generation, poll until done."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple, Optional

import httpx
import rfc8785
import typer
import yaml
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from aethis_cli.client import AethisClient, GenerationModel
from aethis_cli.config import (
    load_project_config,
    make_authed_client,
    read_state,
    resolve_anthropic_key,
    resolve_api_key,
    resolve_deepseek_key,
    write_state,
)
from aethis_cli.errors import AethisAPIError, ConfigError
from aethis_cli.generation_status import format_poll_description
from aethis_cli.output import console, error_panel, info, render_transport_error, success, warn
from aethis_cli.source_safeguards import render_source_safeguards


class AcceptanceContractError(ValueError):
    """A local acceptance-contract input cannot safely reach an engine."""


_MAX_ACCEPTANCE_CASES = 500
_MAX_LEGACY_CASES = 100
_MAX_ACCEPTANCE_ID_LENGTH = 300
_MAX_ACCEPTANCE_LIST_LENGTH = 500


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader with duplicate mappings rejected before Python loses a key."""


def _unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    # Check only keys written in this mapping before applying YAML's standard
    # merge-key expansion.  ``flatten_mapping`` deliberately places inherited
    # keys before explicit ones so explicit values can override an anchor; a
    # duplicate check after flattening would reject that valid legacy shape.
    explicit: set[Any] = set()
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            continue
        key = loader.construct_object(key_node, deep=deep)
        if key in explicit:
            raise yaml.YAMLError(f"duplicate key {key!r}")
        explicit.add(key)
    loader.flatten_mapping(node)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _json_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise AcceptanceContractError(f"acceptance contract contains duplicate key {key!r}")
        out[key] = value
    return out


def _strict_string_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise AcceptanceContractError(f"{path} must be an array of non-empty strings")
    if len(value) > _MAX_ACCEPTANCE_LIST_LENGTH:
        raise AcceptanceContractError(f"{path} must contain at most {_MAX_ACCEPTANCE_LIST_LENGTH} values")
    if any(len(item) > _MAX_ACCEPTANCE_ID_LENGTH for item in value):
        raise AcceptanceContractError(f"{path} values must be at most {_MAX_ACCEPTANCE_ID_LENGTH} code points")
    if len(set(value)) != len(value):
        raise AcceptanceContractError(f"{path} must not contain duplicate values")
    return value


def _normalise_expectations(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AcceptanceContractError(f"{path} must be an object")
    unknown = set(value) - {"pending_reviews", "useful_unknown_fields"}
    if unknown:
        raise AcceptanceContractError(f"{path} contains unsupported key(s): {', '.join(sorted(unknown))}")
    if not value:
        raise AcceptanceContractError(f"{path} must not be empty")
    out: dict[str, Any] = {}
    if "pending_reviews" in value:
        pending = value["pending_reviews"]
        if not isinstance(pending, dict) or set(pending) != {"resolution_fields", "unmapped_count"}:
            raise AcceptanceContractError(
                f"{path}.pending_reviews must contain exactly resolution_fields and unmapped_count"
            )
        count = pending["unmapped_count"]
        if type(count) is not int or not 0 <= count <= _MAX_ACCEPTANCE_LIST_LENGTH:
            raise AcceptanceContractError(
                f"{path}.pending_reviews.unmapped_count must be an integer from 0 to "
                f"{_MAX_ACCEPTANCE_LIST_LENGTH} (not a boolean)"
            )
        out["pending_reviews"] = {
            "resolution_fields": _strict_string_list(
                pending["resolution_fields"], f"{path}.pending_reviews.resolution_fields"
            ),
            "unmapped_count": count,
        }
    if "useful_unknown_fields" in value:
        out["useful_unknown_fields"] = _strict_string_list(
            value["useful_unknown_fields"], f"{path}.useful_unknown_fields"
        )
    return out


def _normalise_test_cases(cases: Any, path: str, *, yaml_shape: bool) -> list[dict[str, Any]]:
    if not isinstance(cases, list) or not cases:
        raise AcceptanceContractError(f"{path} must be a non-empty array")
    if len(cases) > _MAX_ACCEPTANCE_CASES:
        raise AcceptanceContractError(f"{path} must contain at most {_MAX_ACCEPTANCE_CASES} cases")
    out: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, case in enumerate(cases):
        case_path = f"{path}[{index}]"
        if not isinstance(case, dict):
            raise AcceptanceContractError(f"{case_path} must be an object")
        # Legacy scenarios have always allowed descriptive case metadata and
        # projected only name/inputs/expect onto the API request. Preserve that
        # compatibility. The explicit JSON v1 artefact is strict throughout.
        if not yaml_shape:
            allowed = {"name", "field_values", "expected_outcome", "expectations"}
            unknown = set(case) - allowed
            if unknown:
                raise AcceptanceContractError(f"{case_path} contains unsupported key(s): {', '.join(sorted(unknown))}")
        name = case.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise AcceptanceContractError(f"{case_path}.name must be a unique non-empty string")
        names.add(name)
        if yaml_shape:
            values = case.get("inputs", {})
            expect = case.get("expect", {})
            if not isinstance(expect, dict) or set(expect) - {"outcome", "pending_reviews", "useful_unknown_fields"}:
                raise AcceptanceContractError(
                    f"{case_path}.expect may contain only outcome, pending_reviews, and useful_unknown_fields"
                )
            outcome = expect.get("outcome", "eligible")
            expectations = {
                key: expect[key] for key in ("pending_reviews", "useful_unknown_fields") if key in expect
            } or None
        else:
            values = case.get("field_values")
            outcome = case.get("expected_outcome")
            expectations = case.get("expectations")
        if not isinstance(values, dict):
            raise AcceptanceContractError(f"{case_path}.field_values/inputs must be an object")
        if not isinstance(outcome, str) or outcome not in {
            "eligible",
            "not_eligible",
            "undetermined",
        }:
            raise AcceptanceContractError(
                f"{case_path}.expected_outcome must be eligible, not_eligible, or undetermined"
            )
        normal = {"name": name, "field_values": values, "expected_outcome": outcome}
        if expectations is not None:
            normal["expectations"] = _normalise_expectations(expectations, f"{case_path}.expectations")
        out.append(normal)
    return out


def _normalise_bindings(value: Any) -> dict[str, dict[str, bool | None]]:
    if not isinstance(value, dict):
        raise AcceptanceContractError("expected_review_bindings must be an object; null is not valid")
    out: dict[str, dict[str, bool | None]] = {}
    for field, tokens in value.items():
        if (
            not isinstance(field, str)
            or not field.strip()
            or len(field) > _MAX_ACCEPTANCE_ID_LENGTH
            or not isinstance(tokens, dict)
            or not tokens
        ):
            raise AcceptanceContractError(
                "each expected_review_bindings entry needs a non-empty field id of at most "
                f"{_MAX_ACCEPTANCE_ID_LENGTH} code points and a non-empty token object"
            )
        clean: dict[str, bool | None] = {}
        for token, strict in tokens.items():
            if (
                not isinstance(token, str)
                or not token.strip()
                or len(token) > _MAX_ACCEPTANCE_ID_LENGTH
                or (strict is not None and type(strict) is not bool)
            ):
                raise AcceptanceContractError(
                    "review-binding tokens must be non-empty strings of at most "
                    f"{_MAX_ACCEPTANCE_ID_LENGTH} code points mapped to strict true, false, or null"
                )
            clean[token] = strict
        out[field] = clean
    return out


def _load_acceptance_contract(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise AcceptanceContractError(f"cannot read acceptance contract {path}: {exc}") from exc
    if size > 1_000_000:
        raise AcceptanceContractError(f"{path} exceeds 1 MB limit")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise AcceptanceContractError(f"acceptance contract {path} must be UTF-8: {exc}") from exc
    except OSError as exc:
        raise AcceptanceContractError(f"cannot read acceptance contract {path}: {exc}") from exc
    try:
        raw = json.loads(text, object_pairs_hook=_json_no_duplicates)
    except (json.JSONDecodeError, AcceptanceContractError) as exc:
        raise AcceptanceContractError(f"invalid acceptance contract {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise AcceptanceContractError("acceptance contract must be a JSON object")
    unknown = set(raw) - {"contract_version", "test_cases", "expected_review_bindings"}
    if (
        unknown
        or type(raw.get("contract_version")) is not int
        or raw["contract_version"] != 1
        or "test_cases" not in raw
    ):
        raise AcceptanceContractError(
            "acceptance contract must contain only contract_version=1, test_cases, and optional expected_review_bindings"
        )
    out = {
        "contract_version": 1,
        "test_cases": _normalise_test_cases(raw["test_cases"], "test_cases", yaml_shape=False),
    }
    if "expected_review_bindings" in raw:
        out["expected_review_bindings"] = _normalise_bindings(raw["expected_review_bindings"])
    _validate_binding_references(out)
    return out


def _validate_binding_references(contract: dict[str, Any]) -> None:
    catalogue = contract.get("expected_review_bindings")
    if catalogue is None:
        return
    known = set(catalogue)
    for case in contract["test_cases"]:
        fields = (case.get("expectations") or {}).get("pending_reviews", {}).get("resolution_fields", [])
        unknown = sorted(set(fields) - known)
        if unknown:
            raise AcceptanceContractError(
                f"{case['name']!r} references resolution field(s) not in expected_review_bindings: {', '.join(unknown)}"
            )


def _acceptance_contract_digest(contract: dict[str, Any]) -> str:
    """Hash core#656's readback projection with RFC 8785, preserving code points."""
    cases = []
    for case in contract["test_cases"]:
        expect: dict[str, Any] = {"outcome": case["expected_outcome"]}
        if "expectations" in case:
            expect.update(case["expectations"])
        cases.append({"name": case["name"], "inputs": case["field_values"], "expect": expect})
    payload = {
        "version": contract.get("contract_version"),
        "expected_review_bindings": contract.get("expected_review_bindings"),
        "test_cases": cases,
    }
    try:
        canonical = rfc8785.dumps(payload)
    except rfc8785.CanonicalizationError as exc:
        raise AcceptanceContractError(
            f"acceptance contract contains a value outside the RFC 8785 canonical JSON domain: {exc}"
        ) from exc
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class _PreparedTestUpload:
    """Validated test upload frozen before any persistent project mutation.

    ``contract`` is a recursively immutable snapshot of the normalised payload.
    The upload path thaws that snapshot rather than reading or parsing the
    source file again, so the values validated before project creation are the
    values later sent to the engine.
    """

    origin: str
    contract: Mapping[str, Any]
    digest: Optional[str]
    capability: Optional[bool]

    def materialise(self) -> dict[str, Any]:
        value = _thaw_json(self.contract)
        if not isinstance(value, dict):  # construction invariant
            raise RuntimeError("prepared test upload is not an object")
        return value


def _load_scenarios_contract(project_dir: Path) -> tuple[dict[str, Any], str] | None:
    """Load the legacy YAML suite while preserving its non-semantic metadata."""
    tests_path = project_dir / "tests" / "scenarios.yaml"
    if not tests_path.exists():
        return None
    try:
        size = tests_path.stat().st_size
    except OSError as exc:
        raise AcceptanceContractError(f"cannot read scenarios file {tests_path}: {exc}") from exc
    if size > 1_000_000:
        raise AcceptanceContractError(f"{tests_path} exceeds 1 MB limit")
    try:
        text = tests_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise AcceptanceContractError(f"scenarios file {tests_path} must be UTF-8: {exc}") from exc
    except OSError as exc:
        raise AcceptanceContractError(f"cannot read scenarios file {tests_path}: {exc}") from exc
    try:
        raw = yaml.load(text, Loader=_UniqueKeyLoader) or {}
    except yaml.YAMLError as exc:
        raise AcceptanceContractError(f"invalid YAML in {tests_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise AcceptanceContractError(f"{tests_path} must contain a tests array")
    misplaced = {"contract_version", "expected_review_bindings"} & set(raw)
    if misplaced:
        raise AcceptanceContractError(
            f"{tests_path} cannot declare semantic top-level key(s) "
            f"{', '.join(sorted(misplaced))}; use --acceptance-contract with the versioned JSON sidecar"
        )
    if not raw.get("tests"):
        return None
    normalised = _normalise_test_cases(raw["tests"], "tests", yaml_shape=True)
    contract: dict[str, Any] = {"test_cases": normalised}
    if any("expectations" in case for case in normalised):
        contract["contract_version"] = 1
    elif len(normalised) > _MAX_LEGACY_CASES:
        raise AcceptanceContractError(
            f"legacy scenarios uploads are limited to {_MAX_LEGACY_CASES} cases; "
            "use --acceptance-contract for a versioned suite of up to "
            f"{_MAX_ACCEPTANCE_CASES} cases"
        )
    return contract, tests_path.name


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _seal_contract(contract: dict[str, Any]) -> Mapping[str, Any]:
    """Validate JSON transport and freeze one recursively immutable payload."""
    try:
        text = json.dumps(contract, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        text.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise AcceptanceContractError(f"test contract is not valid UTF-8 JSON: {exc}") from exc
    frozen = _freeze_json(contract)
    if not isinstance(frozen, Mapping):  # construction invariant
        raise RuntimeError("test contract did not freeze to an object")
    return frozen


def _prepare_test_upload(
    client: AethisClient,
    project_dir: Path,
    *,
    acceptance_contract_path: Optional[Path] = None,
) -> Optional[_PreparedTestUpload]:
    """Validate, digest and capability-check the planned upload before writes."""
    if acceptance_contract_path is not None:
        contract = _load_acceptance_contract(acceptance_contract_path)
        origin = acceptance_contract_path.name
    else:
        loaded = _load_scenarios_contract(project_dir)
        if loaded is None:
            return None
        contract, origin = loaded

    digest: Optional[str] = None
    if contract.get("contract_version") == 1:
        digest = _acceptance_contract_digest(contract)
        capability = client.supports_test_acceptance_contract()
        if capability is not True:
            raise AcceptanceContractError(
                "this engine does not advertise the complete acceptance-contract v1 envelope; "
                "stopping before replacing tests or changing the project"
            )
    else:
        capability = client.supports_test_replace()

    return _PreparedTestUpload(
        origin=origin,
        contract=_seal_contract(contract),
        digest=digest,
        capability=capability,
    )


def _strict_json_equal(actual: Any, expected: Any) -> bool:
    """Compare JSON values without Python's bool/int equality coercion."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _strict_json_equal(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _strict_json_equal(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
    return actual == expected


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


class UploadSummary(NamedTuple):
    new: int
    reused: int


def _upload_counts(response: dict) -> UploadSummary:
    """Read the engine's authoritative upload disposition."""
    new = response.get("new")
    reused = response.get("reused")
    if not isinstance(new, int) or not isinstance(reused, int):
        raise AethisAPIError(502, "The source upload response did not report integer new/reused counts.")
    return UploadSummary(new=new, reused=reused)


def _collect_source_files(project_dir: Path) -> list[Path]:
    """Every file under the project's ``sources/`` dir (symlink-escape guarded)."""
    sources_dir = project_dir / "sources"
    if not sources_dir.is_dir():
        return []
    root = sources_dir.resolve()
    return sorted(f for f in sources_dir.rglob("*") if f.is_file() and f.resolve().is_relative_to(root))


def _resolve_or_create_project(client: AethisClient, cfg, project_id: Optional[str] = None) -> str:
    """Return the project id, creating (and persisting) a new project when there
    is none or the recorded one is gone from the server."""
    pid = project_id or cfg.project_id
    if pid:
        # Verify the project still exists (may be stale from a different server).
        try:
            client.get_project(pid)
        except AethisAPIError as e:
            if e.status_code == 404:
                info(f"Project {pid} not found on server, creating new project")
                pid = None
            else:
                raise
    if not pid:
        result = client.create_project(cfg.project, cfg.project, "")
        pid = result["project_id"]
        # Reset the uploaded-sources ledger — a fresh project has none.
        write_state(cfg.config_path, {"project_id": pid, "uploaded_sources": {}})
        info(f"Created project {pid}")
    return pid


def _upload_sources(client: AethisClient, pid: str, project_dir: Path) -> UploadSummary:
    """Upload source files new or changed since the last upload, batched in 5s.

    A per-file mtime ledger in ``.aethis/state.json`` keeps this idempotent, so a
    ``discover`` followed by a ``generate`` (or repeated generates) doesn't
    re-push unchanged sources. Edited files replace their active same-name
    predecessor through the engine's reversible source lifecycle.
    """
    files = _collect_source_files(project_dir)
    if not files:
        return UploadSummary(new=0, reused=0)
    root = (project_dir / "sources").resolve()
    ledger = dict(read_state(project_dir).get("uploaded_sources") or {})
    to_upload = []
    for f in files:
        rel = str(f.resolve().relative_to(root))
        mtime = f.stat().st_mtime_ns
        if ledger.get(rel) != mtime:
            to_upload.append(f)
        ledger[rel] = mtime
    if not to_upload:
        return UploadSummary(new=0, reused=0)

    listed = client.list_sources(pid) or {}
    active_by_filename = {
        source.get("filename"): source
        for source in (listed.get("sources") or [])
        if isinstance(source, dict) and source.get("filename") and source.get("status", "active") == "active"
    }
    replacements: list[tuple[Path, dict]] = []
    ordinary: list[Path] = []
    for path in to_upload:
        active = active_by_filename.get(path.name)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if active and active.get("raw_sha256") != digest:
            replacements.append((path, active))
        else:
            ordinary.append(path)

    summary = UploadSummary(new=0, reused=0)
    for batch in _chunks(ordinary, 5):
        counts = _upload_counts(client.upload_sources(pid, batch) or {})
        summary = UploadSummary(new=summary.new + counts.new, reused=summary.reused + counts.reused)

    for path, prior in replacements:
        prior_id = prior.get("source_id")
        if not isinstance(prior_id, str) or not prior_id:
            raise AethisAPIError(502, f"The active source metadata for {path.name} has no source_id.")
        client.update_source(pid, prior_id, status="superseded")
        try:
            response = client.upload_sources(pid, [path]) or {}
        except Exception:
            try:
                client.update_source(pid, prior_id, status="active")
            except Exception as rollback_error:  # noqa: BLE001 — preserve the upload failure
                warn(f"Replacement upload failed and source {prior_id} could not be reactivated: {rollback_error}")
            raise
        sources = response.get("sources") or []
        replacement_id = sources[0].get("source_id") if sources and isinstance(sources[0], dict) else None
        if not isinstance(replacement_id, str) or not replacement_id:
            client.update_source(pid, prior_id, status="active")
            raise AethisAPIError(502, f"The replacement upload for {path.name} returned no source_id.")
        client.update_source(pid, prior_id, status="superseded", superseded_by=replacement_id)
        counts = _upload_counts(response)
        summary = UploadSummary(new=summary.new + counts.new, reused=summary.reused + counts.reused)

    write_state(project_dir, {"uploaded_sources": ledger})
    info(f"Sources: {summary.new} new, {summary.reused} reused")
    return summary


def _load_yaml_file(path: Path) -> dict:
    """Read + parse a project YAML file, failing fast on oversize / bad YAML."""
    if path.stat().st_size > 1_000_000:
        console.print(f"[red]{path} exceeds 1 MB limit[/red]")
        raise typer.Exit(code=1)
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        console.print(f"[red]Invalid YAML in {path}: {e}[/red]")
        raise typer.Exit(code=1)


def _parent_rulebook_dir(project_dir: Path) -> Optional[Path]:
    """Return the enclosing rulebook directory if this project is a member ruleset.

    An explicit ``rulebook:`` key in the ruleset's ``aethis.yaml`` wins — its
    value is a path (relative to the ruleset directory) to the rulebook. Failing
    that, falls back to the scaffold shape ``<rulebook>/rulesets/<ruleset>/`` by
    directory position, so rulebook-level guidance + fields can be propagated
    down into the ruleset's generation.
    """
    cfg_file = project_dir / "aethis.yaml"
    if cfg_file.exists():
        try:
            raw = yaml.safe_load(cfg_file.read_text()) or {}
        except yaml.YAMLError:
            raw = {}
        declared = raw.get("rulebook")
        if declared:
            rb_path = (project_dir / declared).resolve()
            if (rb_path / "aethis.yaml").exists():
                return rb_path
            console.print(
                f"[yellow]aethis.yaml declares rulebook: {declared!r} but no aethis.yaml "
                f"found at {rb_path} — falling back to directory position.[/yellow]"
            )

    parent = project_dir.parent
    if parent.name == "rulesets" and (parent.parent / "aethis.yaml").exists():
        return parent.parent
    return None


def _parse_fields_yaml(path: Path) -> dict:
    """Parse a fields.yaml into an ordered ``{key: field_dict}`` map."""
    raw = _load_yaml_file(path)
    out: dict = {}
    for f in raw.get("fields", []) or []:
        if isinstance(f, dict) and f.get("key"):
            out[f["key"]] = f
    return out


def _field_guidance_lines(key: str, field: dict) -> list[str]:
    """Natural-language guidance derived from a field's label/question/hints.

    Guidance remains useful context for the model, but explicitly authored
    behaviour also rides the field pin and wins deterministically downstream.
    """
    lines: list[str] = []
    if field.get("question"):
        lines.append(f'Ask field "{key}" using this question: {field["question"]}')
    if field.get("label"):
        lines.append(f'Label field "{key}" as: {field["label"]}')
    for hint in field.get("hints", []) or []:
        if hint:
            lines.append(f'Field "{key}": {hint}')
    return lines


# The set of value types a ``fields.yaml`` entry may declare. Mirrors the
# engine's accepted sorts (it normalises case + the long forms below).
VALID_FIELD_TYPES = {"int", "bool", "string", "enum", "date", "duration"}

# The server speaks the long, public-facing type names; ``fields.yaml`` uses the
# short canonical forms. Map server → on-disk so a pulled/discovered field reads
# back the same way a hand-authored one does.
_SERVER_TYPE_TO_YAML = {
    "integer": "int",
    "boolean": "bool",
    "enumeration": "enum",
    "str": "string",
}

# Field-key order written back to ``fields.yaml`` so machine-written files read
# the same as the hand-authored template.
_FIELD_KEY_ORDER = (
    "key",
    "type",
    "label",
    "question",
    "enum_values",
    "value_space",
    "enum_labels",
    "canonical_field",
    "weight",
    "elicitation_owner",
    "injection_source",
    "injection_phase",
    "recoverable_from",
    "x_ui_widget",
    "notes",
    "hints",
)

# Field-spec properties the engine must advertise before an authored value for
# them is worth sending. An engine that does not model a property ignores it,
# so the upload succeeds and the metadata is gone.
_ENGINE_GATED_FIELD_KEYS = (
    "enum_labels",
    "canonical_field",
    "label",
    "question",
    "weight",
    "elicitation_owner",
    "injection_source",
    "injection_phase",
    "recoverable_from",
    "x_ui_widget",
    "notes",
)

# Rulebook vocabulary is intentionally slimmer than a generation pin. Keep its
# capability gate scoped to properties that RulebookFieldSpec actually carries;
# otherwise a project-only property in a shared authoring file would make the
# rulebook command refuse an engine that is fully compatible with that route.
_RULEBOOK_GATED_FIELD_KEYS = ("enum_labels", "canonical_field")


def _normalise_field_type(t: Optional[str]) -> str:
    """Fold a server/long type name into the short ``fields.yaml`` form."""
    if not t:
        return "string"
    low = t.strip().lower()
    return _SERVER_TYPE_TO_YAML.get(low, low)


def _safe_field_type(raw_type: Optional[str], enum_values: Optional[list]) -> str:
    """A field ``type`` guaranteed to pass validation when written to disk.

    Server/discovery payloads can carry a type we don't model (it would write a
    file the next ``validate``/``generate`` rejects) or an ``enum`` with no
    values (not a representable enum on disk). Both fall back to ``string``.
    """
    t = _normalise_field_type(raw_type)
    if t not in VALID_FIELD_TYPES:
        return "string"
    if t == "enum" and not enum_values:
        return "string"
    return t


def validate_fields_list(fields: list) -> list[str]:
    """Return human-readable validation errors for a ``fields.yaml`` field list.

    Checks: every entry has a key, no duplicate keys, the ``type`` is one of
    :data:`VALID_FIELD_TYPES`, and ``enum`` types declare exactly one member
    source — inline ``enum_values`` XOR a named ``value_space`` reference
    (aethis-core#424). ``enum_labels`` must be a slug→label mapping on an enum
    field, and where the members are declared inline it may not label a member
    the field does not have — a mislabelled key is silently inert on the
    engine, so it is caught here. ``canonical_field`` must be a non-empty
    string. An empty return means the list is valid.
    """
    errors: list[str] = []
    seen: set[str] = set()
    for i, f in enumerate(fields or []):
        if not isinstance(f, dict):
            errors.append(f"Field #{i + 1} is not a mapping.")
            continue
        key = f.get("key")
        if not key:
            errors.append(f"Field #{i + 1} is missing a 'key'.")
            continue
        if key in seen:
            errors.append(f"Duplicate field key: {key!r}.")
        seen.add(key)
        ftype = (f.get("type") or f.get("sort") or "").strip().lower()
        if ftype not in VALID_FIELD_TYPES:
            errors.append(
                f"Field {key!r} has invalid type {f.get('type') or f.get('sort')!r} "
                f"(must be one of: {', '.join(sorted(VALID_FIELD_TYPES))})."
            )
        if f.get("value_space") and f.get("enum_values"):
            errors.append(
                f"Field {key!r} declares both value_space and enum_values — they are "
                f"mutually exclusive; the named reference is authoritative, so drop the "
                f"inline members."
            )
        if f.get("value_space") and ftype != "enum":
            errors.append(f"Field {key!r} declares value_space but is type {ftype!r} — only enum fields may.")
        if ftype == "enum" and not f.get("enum_values") and not f.get("value_space"):
            errors.append(f"Field {key!r} is type 'enum' but declares no enum_values (or value_space).")
        errors.extend(_validate_display_metadata(key, f, ftype))
    return errors


def _validate_display_metadata(key: str, f: dict, ftype: str) -> list[str]:
    """Validate the authored display metadata on one field entry.

    ``enum_labels`` pairs each member slug with the wording a human should see;
    ``canonical_field`` names the storage key the field's value belongs to.
    Both ride the field pin to the engine, which treats their contents as
    opaque — so a label attached to a slug the field does not have is accepted
    and never rendered. Where the members are known locally (inline
    ``enum_values``) that is caught here; where they are a named reference the
    members live on the registry and only the shape is checked.
    """
    errors: list[str] = []
    if "enum_labels" in f:
        labels = f["enum_labels"]
        if labels is None:
            # Nullable on the engine's model, so an explicit null is a legal
            # way to say "no labels" and is transmitted as authored.
            pass
        elif not isinstance(labels, dict):
            errors.append(f"Field {key!r} declares enum_labels but it is not a mapping of member → label.")
        else:
            # An empty map is a legitimate declaration — the engine keeps it —
            # so the checks below simply have nothing to say about it.
            if ftype != "enum":
                errors.append(f"Field {key!r} declares enum_labels but is type {ftype!r} — only enum fields may.")
            # Members are compared as text, so a non-text key can never match
            # one and is reported as its own fault rather than as a member the
            # field does not declare.
            non_text = sorted((repr(m) for m in labels if not isinstance(m, str)), key=str)
            if non_text:
                errors.append(f"Field {key!r} has non-text enum_labels member(s): {', '.join(non_text)}.")
            bad = sorted(
                (repr(m) for m, label in labels.items() if not isinstance(label, str) or not label.strip()), key=str
            )
            if bad:
                errors.append(f"Field {key!r} has empty or non-text enum_labels for: {', '.join(bad)}.")
            members = f.get("enum_values")
            if isinstance(members, list):
                unknown = sorted(m for m in labels if isinstance(m, str) and m not in members)
                if unknown:
                    errors.append(
                        f"Field {key!r} labels member(s) it does not declare: {', '.join(unknown)} — "
                        f"a label for a member the field does not have is never shown."
                    )
    if "canonical_field" in f:
        canonical = f["canonical_field"]
        # Null is legal (the property is nullable); empty text is not. The
        # engine accepts `""` — it is opaque there — but it publishes a pairing
        # no consumer can resolve, so it is refused here rather than shipped.
        if canonical is not None and not isinstance(canonical, str):
            errors.append(f"Field {key!r} declares canonical_field but it is not text.")
        elif isinstance(canonical, str) and not canonical.strip():
            errors.append(
                f"Field {key!r} declares an empty canonical_field. The engine would accept it — the value is "
                f"opaque there — but it publishes a pairing no consumer can resolve, so it is refused here. "
                f"Omit the key, or use null, to say there is no pairing."
            )
    return errors


def _field_to_yaml_dict(field: dict) -> dict:
    """Serialise a field in canonical key order, dropping empties.

    Any key we don't model (e.g. a hand-authored ``description`` or ``weight``)
    is preserved after the known keys so a round-trip write never silently
    discards it. ``sort`` is folded into ``type`` and not re-emitted.

    The authored display metadata is kept on **presence**, not truthiness: an
    explicit ``enum_labels: {}`` is a declaration the engine preserves, so
    dropping it here as an "empty" would un-author it on the first pull.
    """
    out: dict = {}
    for k in _FIELD_KEY_ORDER:
        if k in _ENGINE_GATED_FIELD_KEYS:
            if k in field:
                out[k] = field[k]
            continue
        v = (field.get("type") or field.get("sort")) if k == "type" else field.get(k)
        if v in (None, "", [], {}):
            continue
        out[k] = v
    for k, v in field.items():
        if k in out or k == "sort" or v in (None, "", [], {}):
            continue
        out[k] = v
    return out


def _write_fields_yaml(path: Path, field_map: dict) -> None:
    """Serialise an ordered ``{key: field}`` map back to ``fields.yaml``."""
    payload = {"fields": [_field_to_yaml_dict(f) for f in field_map.values()]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, default_flow_style=False))


def _merged_field_map(project_dir: Path) -> dict:
    """The effective field vocabulary for a project.

    For a member section of a rulebook, the universe of fields is the
    section's OWN ``fields.yaml``; the enclosing rulebook's definitions
    OVERRIDE its own on shared keys (the canonical definition wins) but a
    rulebook-only field is never added to a section's pin. Pinning the
    rulebook's cross-section fields onto every member demanded fields the
    section's rules never author — historically the engine dropped them
    silently (every published referees_identity build was missing 3-5 of
    them), and the loud pin-presence gate (aethis-core#428) now correctly
    fails such a generation. The pin must be the section's own contract.

    For a standalone project or the rulebook project itself, the own file is
    the whole universe, unchanged.
    """
    own_fields = project_dir / "fields" / "fields.yaml"
    own = _parse_fields_yaml(own_fields) if own_fields.exists() else {}

    rb_map: dict = {}
    rb_dir = _parent_rulebook_dir(project_dir)
    if rb_dir is not None:
        rb_fields = rb_dir / "fields" / "fields.yaml"
        if rb_fields.exists():
            rb_map = _parse_fields_yaml(rb_fields)

    if not own:
        # No own declaration: a section relying on discovery pins nothing
        # (pinning another layer's fields was never a real contract), while a
        # rulebook-level project has no parent and falls through to {} anyway.
        return {}

    return {key: rb_map.get(key, field) for key, field in own.items()}


def _local_value_space_files(project_dir: Path) -> dict:
    """Locally-authored value-space registry files, indexed by declared name.

    A registry file is the wire-form upsert payload (``name``, ``members``,
    ``provenance`` — extra generator keys like ``derived_from`` are ignored),
    found under a ``value_spaces/`` or ``shared/value_spaces/`` directory of
    the project, its enclosing rulebook, or any ancestor directory UP TO THE
    REPO ROOT (form-an keeps them at the repo root's ``shared/value_spaces/``).
    The walk stops at the first ancestor containing ``.git`` — a same-named
    yaml lying above the repo must never be silently PUT. Nearest wins.
    """
    search_dirs: list[Path] = []
    current = project_dir.resolve()
    while True:
        search_dirs.append(current)
        if (current / ".git").exists() or current.parent == current:
            break
        current = current.parent
    rb_dir = _parent_rulebook_dir(project_dir)
    if rb_dir is not None and rb_dir not in search_dirs:
        search_dirs.insert(1, rb_dir)

    spaces: dict = {}
    for d in search_dirs:
        for sub in (d / "value_spaces", d / "shared" / "value_spaces"):
            if not sub.is_dir():
                continue
            for path in sorted(sub.glob("*.yaml")):
                raw = _load_yaml_file(path)
                name = raw.get("name")
                if name and name not in spaces:
                    spaces[name] = raw
    return spaces


def _api_error_message(e: AethisAPIError) -> str:
    """A human-readable line from an API error whose detail may be structured.

    The engine's value-space conflicts carry a dict detail (reason_code +
    message + versions); printing the dict repr buries the one sentence the
    author needs (review advisory, PR #110).
    """
    if isinstance(e.detail, dict):
        return str(e.detail.get("message") or e.detail)
    return str(e.detail)


def _sync_value_spaces(client: AethisClient, project_dir: Path, referenced: set) -> None:
    """PUT every locally-authored space a pin references, before spec-set.

    The base version comes from the sync state (``.aethis/state.json`` →
    ``value_space_sync``), so a stale checkout gets the engine's 409 ("space
    moved under you — pull first") instead of silently regressing the shared
    vocabulary. ANY non-2xx aborts before ``set_field_spec`` (design note
    DX-6): a pre-#424 engine is extra-ignore on ExpectedFieldSpec, so
    proceeding would drop the pin and the model would free-author the
    members — the exact failure the reference exists to remove.
    """
    local = _local_value_space_files(project_dir)
    sync_state = dict(read_state(project_dir).get("value_space_sync") or {})

    for name in sorted(referenced):
        space = local.get(name)
        if space is None:
            # No local file: the space may be registered by another project —
            # but the abort guarantee must not go vacuous on exactly this
            # lane, so PROBE the engine rather than proceed on hope. A 2xx
            # proves both the registry capability and tenant resolution; any
            # non-2xx (route missing, unknown/foreign space, outage) aborts —
            # a pre-#424 engine would 200 the spec-set and silently drop the
            # pin (review fix, PR #110).
            try:
                probe = client.get_value_space(name)
            except AethisAPIError as e:
                if e.status_code == 404:
                    console.print(
                        f"[red]Value space '{name}' is not resolvable on the engine "
                        f"(GET /api/v1/public/value-spaces/{name} returned 404): either it is "
                        f"not registered for this tenant, or the engine lacks the "
                        f"value-spaces registry entirely.[/red]"
                    )
                    console.print(
                        "[red]Stopping before the field spec push. Add a local registry "
                        "file (value_spaces/ or shared/value_spaces/*.yaml declaring it) "
                        "so this run can register it, or upgrade the engine.[/red]"
                    )
                else:
                    console.print(
                        f"[red]Could not verify value space '{name}' on the engine: {_api_error_message(e)}[/red]"
                    )
                    console.print("[red]Stopping before the field spec push.[/red]")
                raise typer.Exit(code=1)
            info(
                f"Value space '{name}': no local registry file; engine serves it at "
                f"v{probe.get('version')} — proceeding."
            )
            continue
        try:
            result = client.put_value_space(
                name=name,
                members=list(space.get("members") or []),
                provenance=space.get("provenance") or {},
                base_version=sync_state.get(name),
            )
        except AethisAPIError as e:
            if e.status_code == 404:
                console.print(
                    f"[red]The engine does not expose the value-spaces registry "
                    f"(PUT /api/v1/public/value-spaces/{name} returned 404).[/red]"
                )
                console.print(
                    "[red]Stopping before the field spec push: an engine without this "
                    "capability silently drops the value_space pin, and the model would "
                    "free-author the field's members. Upgrade the engine, or remove the "
                    "value_space reference from fields.yaml.[/red]"
                )
            else:
                console.print(f"[red]Could not sync value space '{name}': {_api_error_message(e)}[/red]")
                console.print(
                    "[red]Stopping before the field spec push — a pin referencing an "
                    "unsynced space would fail, or bind to stale members.[/red]"
                )
            raise typer.Exit(code=1)
        # Record each synced version the moment its PUT lands — a later
        # space's abort must not lose this one's base version (review
        # advisory, PR #110: the retry would otherwise 409 against the
        # author's own successful write).
        sync_state[name] = result.get("version")
        write_state(project_dir, {"value_space_sync": dict(sync_state)})
        verb = "synced" if result.get("created") else "already current at"
        info(f"Value space '{name}' {verb} v{result.get('version')} ({len(space.get('members') or [])} members)")


def _upload_field_vocabulary(client: AethisClient, pid: str, project_dir: Path) -> None:
    """Push the field vocabulary for this project.

    The pin universe is the project's OWN ``fields.yaml``; for a member
    section the enclosing rulebook's definition overrides shared keys (the
    canonical definition wins) but never adds keys — see
    ``_merged_field_map``. Pins the expected field keys/types via
    ``/fields/spec`` and routes each field's label/question/hints through
    guidance.
    """
    # Fail fast on a malformed vocabulary before we mutate server state. Validate
    # each contributing file's RAW list so duplicate keys *within a file* surface
    # — the merged map would silently collapse them. A key shared between the
    # rulebook and the ruleset is intentional (rulebook wins), not a duplicate.
    rb_dir = _parent_rulebook_dir(project_dir)
    contributing = [project_dir / "fields" / "fields.yaml"]
    if rb_dir is not None:
        contributing.insert(0, rb_dir / "fields" / "fields.yaml")
    for path in contributing:
        if not path.exists():
            continue
        errors = validate_fields_list(_load_yaml_file(path).get("fields") or [])
        if errors:
            console.print(f"[red]{path} is invalid:[/red]")
            for e in errors:
                console.print(f"  [red]✗[/red] {e}")
            raise typer.Exit(code=1)

    field_map = _merged_field_map(project_dir)
    if not field_map:
        return

    expected_fields: list[dict] = []
    guidance_lines: list[str] = []
    for key, field in field_map.items():
        spec = {"key": key, "sort": field.get("type") or field.get("sort")}
        if field.get("enum_values"):
            spec["enum_values"] = field["enum_values"]
        if field.get("value_space"):
            spec["value_space"] = field["value_space"]
        # PRESENCE, not truthiness. `enum_labels: {}` is a declaration the
        # engine keeps and republishes as `{}` — distinct from the property
        # being absent — so testing the value would drop exactly the shape the
        # engine went to the trouble of preserving. Explicit `null` is accepted
        # too (both properties are nullable on the engine's model), and it is
        # likewise transmitted as authored rather than silently discarded.
        for prop in _ENGINE_GATED_FIELD_KEYS:
            if prop in field:
                value = field[prop]
                spec[prop] = dict(value) if isinstance(value, dict) else value
        # In fields.yaml, omitting a question on a declared non-applicant fact
        # is intentional: there is no applicant question. Make that absence
        # explicit on the wire so a model-invented question is cleared rather
        # than mistaken for legacy abstention.
        if field.get("elicitation_owner") not in (None, "applicant") and "question" not in field:
            spec["question"] = None
        expected_fields.append(spec)
        guidance_lines.extend(_field_guidance_lines(key, field))

    # Registry sync BEFORE spec-set (aethis-core#424, design note DX-6): the
    # engine resolves a value_space reference at the spec-set boundary, so
    # every locally-authored space must exist there first — and a pre-#424
    # engine, which ignores unknown ExpectedFieldSpec keys, must be caught
    # HERE rather than silently dropping the pin and letting the model
    # free-author the vocabulary. Any non-2xx aborts before set_field_spec.
    referenced = {f["value_space"] for f in field_map.values() if f.get("value_space")}
    if referenced:
        _sync_value_spaces(client, project_dir, referenced)

    check_display_metadata_support(client, expected_fields)

    client.set_field_spec(pid, expected_fields)
    for line in guidance_lines:
        client.add_guidance(pid, line)
    info(f"Set field spec ({len(expected_fields)} field(s))")


def check_display_metadata_support(client: AethisClient, fields: list[dict], *, rulebook: bool = False) -> None:
    """Refuse to push authored field metadata an engine will throw away.

    An engine that predates a field-spec property does not reject it — it
    ignores it, so the upload succeeds, the generation runs, and the labels
    (or the ownership/question contract) are simply gone. That is the one failure this
    guard exists to make loud, and it fires only for the fields that actually
    declare something: a project with none is untouched.

    An engine whose schema cannot be read at all is a different answer from one
    that answered "no", and only the second is evidence. The first is reported
    and the upload proceeds.

    ``rulebook`` selects which model the engine is asked about — a rulebook
    field entry and a project field pin are different models and an engine may
    carry the properties on one and not the other, so each upload path asks
    about the one it actually posts.
    """
    gated_keys = _RULEBOOK_GATED_FIELD_KEYS if rulebook else _ENGINE_GATED_FIELD_KEYS
    declared = sorted({k for f in fields if isinstance(f, dict) for k in gated_keys if k in f})
    if not declared:
        return
    advertised = client.rulebook_field_spec_properties() if rulebook else client.expected_field_spec_properties()
    if advertised is None:
        console.print(
            f"[yellow]Could not read the engine's field-spec schema, so it is unknown whether it "
            f"keeps {', '.join(declared)}. Proceeding — an engine that does not model them accepts "
            f"the upload and discards them silently, so verify the published schema afterwards.[/yellow]"
        )
        return
    missing = [k for k in declared if k not in advertised]
    if not missing:
        return
    console.print(f"[red]This engine does not carry {', '.join(missing)} on a field spec ({client.base_url}).[/red]")
    console.print(
        "[red]Stopping before the push: the upload would succeed and the authored values would "
        "be dropped, leaving the published schema without them. Upgrade the engine, or remove "
        "those keys from fields.yaml.[/red]"
    )
    raise typer.Exit(code=1)


def _upload_rulebook_guidance(client: AethisClient, pid: str, project_dir: Path) -> None:
    """Propagate the enclosing rulebook's guidance hints into this ruleset."""
    rb_dir = _parent_rulebook_dir(project_dir)
    if rb_dir is None:
        return
    rb_hints = rb_dir / "guidance" / "hints.yaml"
    if not rb_hints.exists():
        return
    raw = _load_yaml_file(rb_hints)
    count = 0
    for hint in raw.get("hints", []) or []:
        if not hint:
            continue
        text = hint if isinstance(hint, str) else hint.get("text", "")
        if text:
            client.add_guidance(pid, text)
            count += 1
    if count:
        info(f"Propagated {count} rulebook guidance hint(s)")


def generate(
    model: Optional[GenerationModel] = typer.Option(None, "--model", help="Authoring model (default: claude-sonnet-5)"),
    project_id: Optional[str] = typer.Option(None, "--project-id", "-p"),
    poll: bool = typer.Option(True, "--poll/--no-poll", help="Poll until generation completes"),
    timeout: int = typer.Option(600, "--timeout", "-t", help="Polling timeout in seconds"),
    mode: str = typer.Option(
        "fresh",
        "--mode",
        help="fresh = author from scratch; refine = minimal edit seeded from the active ruleset",
    ),
    seed_ruleset_id: Optional[str] = typer.Option(
        None,
        "--seed-ruleset-id",
        help="Ruleset to seed a refine from (defaults to the section's active ruleset)",
    ),
    no_publish: bool = typer.Option(
        False,
        "--no-publish",
        help="Leave the generated ruleset unpublished (a draft) instead of activating it",
    ),
    acceptance_contract: Optional[Path] = typer.Option(
        None,
        "--acceptance-contract",
        help="Versioned JSON acceptance contract to atomically replace project tests",
    ),
) -> None:
    """Upload sources + guidance, trigger ruleset generation, and poll until done."""
    _run_generate(
        project_id=project_id,
        poll=poll,
        timeout=timeout,
        mode=mode,
        seed_ruleset_id=seed_ruleset_id,
        no_publish=no_publish,
        model=model,
        acceptance_contract=acceptance_contract,
    )


def _run_generate(
    *,
    project_id: Optional[str],
    poll: bool,
    timeout: int,
    mode: str = "fresh",
    seed_ruleset_id: Optional[str] = None,
    extra_hint: Optional[str] = None,
    no_publish: bool = False,
    model: Optional[GenerationModel] = None,
    acceptance_contract: Optional[Path] = None,
) -> None:
    """Shared machinery for ``aethis generate`` and ``aethis refine``.

    ``no_publish`` defaults off, and ``aethis refine`` does not pass it: this
    is the plumbing for one flag on one command, not a change of default for
    everything that shares the machinery.
    """
    if model is not None:
        try:
            model = GenerationModel(model)
        except ValueError:
            console.print("[red]Unsupported authoring model. Use claude-sonnet-5 or deepseek-flash.[/red]")
            raise typer.Exit(code=2) from None
    try:
        cfg = load_project_config()
        api_key = resolve_api_key(cfg)
        anthropic_key = None if model == GenerationModel.deepseek else resolve_anthropic_key(cfg)
        deepseek_key = resolve_deepseek_key(cfg) if model == GenerationModel.deepseek else None
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    client = make_authed_client(api_key, cfg.base_url, anthropic_key=anthropic_key)
    project_dir = cfg.config_path

    # Fail-fast on empty sources: generation without any source documents wastes
    # 60-120s on the server and produces a cryptic LLM failure.
    if not _collect_source_files(project_dir):
        console.print(
            f"[red]No source documents found in {project_dir / 'sources'}.[/red]\n"
            "[dim]Add at least one source file (.md, .txt, .pdf) before running 'aethis generate'.[/dim]"
        )
        raise typer.Exit(code=1)

    # Held outside the try so the transport handler can tell "this run never
    # recorded an id" from "it did, and the failure came afterwards".
    outcome: Optional[GenerationOutcome] = None

    try:
        # Parse, validate, canonicalise and capability-check the complete test
        # plan before project creation, guidance, source, field or test writes.
        # The frozen snapshot is the payload uploaded later in this run.
        prepared_tests = _prepare_test_upload(
            client,
            project_dir,
            acceptance_contract_path=acceptance_contract,
        )
        pid = _resolve_or_create_project(client, cfg, project_id)

        # Refinement hint (aethis refine --hint): add before regenerating so it
        # informs the minimal edit.
        if extra_hint:
            client.add_guidance(pid, extra_hint)
            info("Added refinement hint")

        # Upload new/changed source files (idempotent across invocations).
        _upload_sources(client, pid, project_dir)

        # Upload guidance hints
        hints_path = project_dir / "guidance" / "hints.yaml"
        if hints_path.exists():
            if hints_path.stat().st_size > 1_000_000:
                console.print(f"[red]{hints_path} exceeds 1 MB limit[/red]")
                raise typer.Exit(code=1)
            try:
                raw = yaml.safe_load(hints_path.read_text()) or {}
            except yaml.YAMLError as e:
                console.print(f"[red]Invalid YAML in {hints_path}: {e}[/red]")
                raise typer.Exit(code=1)
            hints = raw.get("hints", [])
            count = 0
            for hint in hints:
                if not hint:
                    continue
                if isinstance(hint, str):
                    client.add_guidance(pid, hint)
                else:
                    text = hint.get("text", "")
                    if text:
                        process_type = hint.get("process_type", "rule_generation")
                        client.add_guidance(pid, text, process_type=process_type)
                count += 1
            if count:
                info(f"Added {count} guidance hint(s)")

        # Propagate rulebook-level guidance + push the field vocabulary. A field
        # (e.g. date of birth) defined once at the rulebook level flows down here
        # so the end user is only asked for it once. Rulebook fields win.
        _upload_rulebook_guidance(client, pid, project_dir)
        _upload_field_vocabulary(client, pid, project_dir)

        # Upload the exact test payload prepared before any project mutation.
        _upload_prepared_test_cases(client, pid, prepared_tests)

        # Trigger generation
        if mode == "refine":
            info("Refining: seeding from the active ruleset and making the minimal edit to fix failing tests")
        generation_options = {}
        if model is not None:
            generation_options["model"] = model
        if deepseek_key:
            generation_options["deepseek_key"] = deepseek_key
        job = client.generate(pid, mode=mode, seed_ruleset_id=seed_ruleset_id, **generation_options)
        write_state(project_dir, {"project_id": pid, "job_id": job["job_id"]})
        info(f"Generation queued (job={job['job_id']})")
        # Surface the remaining generate budget from the POST's X-RateLimit-*
        # headers (epic #552) — captured now, before polling /status overwrites
        # it with the `read` class. `aethis usage` shows the full picture.
        _rl = client.last_rate_limit
        if _rl and _rl.get("class") == "generate":
            _rem = _rl.get("remaining", 0)
            _noun = "generation" if _rem == 1 else "generations"
            _style = "yellow" if _rem <= 5 else "dim"
            console.print(f"[{_style}]{_rem} {_noun} left in the current 24h window.[/{_style}]")

        if not poll:
            console.print("Use 'aethis status' to check progress.")
            return

        # Poll with progress spinner
        outcome = _poll_until_done(client, pid, project_dir, timeout, no_publish=no_publish)

        # Surface how the produced field vocabulary compares to what was pinned,
        # rather than letting any drift pass silently. The comparison is always
        # against the artefact THIS run produced — never whatever id happens to
        # be on disk, which after an unsuccessful run names an earlier
        # generation and would make a stale schema read as this one's.
        _report_field_diff(client, outcome.ruleset_id, project_dir, value_spaces_resolved=outcome.value_spaces_resolved)

        if outcome.status != "success":
            _invalidate_stale_pointer(project_dir, outcome.status)
            raise typer.Exit(code=1)

    except AcceptanceContractError as e:
        console.print(f"[red]Acceptance contract rejected: {e}[/red]")
        raise typer.Exit(code=1)

    except AethisAPIError as e:
        error_panel(e)
        # An API error anywhere in the run — including mid-poll, where the job
        # itself may well have succeeded — leaves the recorded id naming an
        # earlier generation. Nothing has been ruled, so the id stands; but it
        # is named, because otherwise this ending is the one that reintroduces
        # the silent stale `fields pull`.
        _invalidate_stale_pointer(project_dir, "error", produced_id=outcome.ruleset_id if outcome else None)
        raise typer.Exit(code=1)

    except httpx.HTTPError as e:
        # The ending the handler above was written for and could not catch.
        # `AethisClient._request` wraps only HTTP *status* failures, so a
        # dropped connection, a DNS failure, a TLS error or a read timeout
        # raises `httpx.HTTPError` and unwound straight past it to the
        # boundary in `main.py` — skipping the invalidation, so the stale
        # pointer was kept (right) and never named (wrong, and silently). That
        # is the failure most likely to interrupt a long poll, so the guard was
        # missing on the path that needs it most (#122).
        #
        # Rendered here rather than re-raised so the error stays ahead of the
        # warning explaining what it cost; `main.py` renders the same line from
        # the same helper for every command that has no cleanup of its own.
        render_transport_error(e, base_url=cfg.base_url)
        _invalidate_stale_pointer(project_dir, "error", produced_id=outcome.ruleset_id if outcome else None)
        raise typer.Exit(code=1)


def _upload_test_cases(
    client: AethisClient,
    pid: str,
    project_dir: Path,
    *,
    acceptance_contract_path: Optional[Path] = None,
) -> None:
    """Prepare and upload tests for isolated callers and compatibility tests.

    The generate/refine path prepares before all project mutation and calls
    :func:`_upload_prepared_test_cases` directly. This wrapper retains the
    existing helper surface for focused upload use.
    """
    prepared = _prepare_test_upload(
        client,
        project_dir,
        acceptance_contract_path=acceptance_contract_path,
    )
    _upload_prepared_test_cases(client, pid, prepared)


def _upload_prepared_test_cases(
    client: AethisClient,
    pid: str,
    prepared: Optional[_PreparedTestUpload],
) -> None:
    """Upload one immutable plan already validated before project mutation.

    The file is the authoritative suite, so the upload replaces what is on the
    project rather than adding to it. That is not free: uploading the same file
    twice used to leave two copies of every case, and duplicates do not error —
    they inflate the denominator of every pass rate, so a run reports a total
    that looks like a result and is partly copies of itself.

    Capability answers are part of ``prepared``. This function performs no
    filesystem reads, parsing, canonicalisation or capability probes.
    """
    if prepared is None:
        return

    contract = prepared.materialise()
    normalised = contract["test_cases"]
    if contract.get("contract_version") == 1:
        if prepared.digest is None or prepared.capability is not True:  # construction invariant
            raise RuntimeError("acceptance-contract upload was not fully prepared")
        kwargs: dict[str, Any] = {"replace": True, "contract_version": 1}
        if "expected_review_bindings" in contract:
            kwargs["expected_review_bindings"] = contract["expected_review_bindings"]
        result = client.add_tests(pid, normalised, **kwargs) or {}
        readback = client.get_project(pid) or {}
        readback_version = readback.get("authoring_acceptance_contract_version")
        if (
            type(readback_version) is not int
            or readback_version != 1
            or not _strict_json_equal(
                readback.get("expected_review_bindings"),
                contract.get("expected_review_bindings"),
            )
            or readback.get("authoring_acceptance_contract_digest") != prepared.digest
        ):
            raise AcceptanceContractError(
                "engine did not return the exact acceptance-contract v1 readback after replacement; "
                "stopping before generation"
            )
        added = result.get("added", len(normalised))
        replaced = result.get("replaced", 0)
        info(
            f"Verified acceptance contract: uploaded {added} test case(s) from {prepared.origin} — "
            f"{replaced} replaced ({prepared.digest})"
        )
        return

    supported = prepared.capability
    if supported:
        result = client.add_tests(pid, normalised, replace=True) or {}
        added = result.get("added", len(normalised))
        replaced = result.get("replaced", 0)
        # The replaced count is the destructive half of an idempotent upload:
        # it is how many cases this run removed from the project. Printed
        # always, including the reassuring zero of a first upload.
        info(f"Uploaded {added} test case(s) from {prepared.origin} — {replaced} replaced")
        return

    client.add_tests(pid, normalised)
    info(f"Added {len(normalised)} test case(s)")
    reason = (
        "this engine does not offer it"
        if supported is False
        else "the engine's API schema could not be read, so its support could not be confirmed"
    )
    warn(
        f"Test cases were APPENDED, not replaced — {reason}. "
        f"Any cases already on the project are still there, so running this again adds "
        f"another copy of all {len(normalised)}. Duplicates do not fail: they inflate the "
        f"total every pass rate is measured against. Remove the extra copies on the "
        f"project, or point at an engine that supports replacing them."
    )


def _report_field_diff(
    client: AethisClient,
    ruleset_id: Optional[str],
    project_dir: Path,
    value_spaces_resolved: Optional[dict] = None,
) -> None:
    """After a generate, print pinned-vs-produced field drift loudly.

    For a field pinned to a named ``value_space`` (aethis-core#424) the
    expected members come from the engine registry AT THE VERSION THE JOB
    RESULT STAMPED (``value_spaces_resolved``) — never the "pins no
    enum_values ⇒ opts out" path, which on exactly the migrated fields would
    print success with zero member verification. An unverifiable referenced
    field is said out loud, and a version advance between sync and generation
    is flagged, never invisible (design note C3, C5(d)).

    Compares the fields pinned locally (``fields.yaml`` + any enclosing
    rulebook) against the fields the engine actually produced in the ruleset
    schema — both the *key set* and, for ENUMs, the *member set*.

    The member half matters as much as the key half. Generation can return a
    field that is present and correctly typed while its enum members have
    grown a value nobody pinned; for an escape-hatch sentinel (defect-shapes
    DS-55) one extra member re-arms the exploit the pin existed to remove, and
    no golden test can catch it — a golden occupying the hostile cell would
    *be* the exploit. Checking keys alone printed "all N pinned field(s) were
    produced" over exactly that schema five times in a row (aethis-core#421).

    ``ruleset_id`` is the artefact *this* run produced, or ``None`` when the run
    produced none. There is deliberately no fallback to the last recorded id: a
    diff against an earlier generation is worse than no diff, because it reads
    exactly like one of this run.

    Never fails the command — including when the API becomes unreachable
    mid-diff. This runs *after* the new id has been recorded, so letting a
    transport error escape here made the caller's stale-pointer guard fire on a
    run that had succeeded and recorded the right id: the CLI asserted the
    correct pointer was stale and exited 1. A schema that cannot be read must not turn a
    successful generation into an error — but never silent either. Both "there
    was nothing to compare" and "the comparison could not be made" are said out
    loud, because the alternative is a run that prints no verdict at all and is
    indistinguishable from a clean one.
    """
    pinned_map = _merged_field_map(project_dir)
    pinned = set(pinned_map.keys())
    if not pinned:
        return
    if not ruleset_id:
        warn(
            "No ruleset was recorded for this run, so the pinned-vs-produced field "
            "diff was not computed. Nothing was compared against an earlier ruleset."
        )
        return
    try:
        schema = client.get_schema(ruleset_id)
    except (AethisAPIError, httpx.HTTPError) as e:
        warn(
            f"Could not read the schema of ruleset {ruleset_id} ({_error_reason(e)}), so the "
            f"pinned-vs-produced field diff was not computed. The draft may no "
            f"longer be retrievable."
        )
        return
    schema_fields = schema.get("fields", []) or []
    produced = {f.get("field_id") for f in schema_fields if f.get("field_id")}
    produced_members = {f["field_id"]: set(f.get("enum_values") or []) for f in schema_fields if f.get("field_id")}

    missing = sorted(pinned - produced)
    extra = sorted(produced - pinned)

    # Member-set drift, per field, by exact equality in both directions.
    # An INLINE field that pins no enum_values opts out — that is the only
    # opt-out; a field pinned to a value_space is verified against the
    # registry below, never opted out.
    #
    # A produced field carrying NO members is the loudest case, not an exempt
    # one: every pinned member was dropped, or the field came back as something
    # other than an enum. Skipping it (as an earlier revision did, reading an
    # empty set as "nothing to compare") reported the worst possible outcome as
    # "all N pinned field(s) were produced".
    member_drift: list[tuple[str, list[str], list[str]]] = []
    verified_lines: list[str] = []
    ref_problems: list[str] = []
    sync_state = read_state(project_dir).get("value_space_sync") or {}
    for key, field in sorted(pinned_map.items()):
        space_name = field.get("value_space")
        if space_name:
            if key not in produced_members:
                continue  # already reported under `missing`
            resolution = (value_spaces_resolved or {}).get(key)
            if not resolution:
                ref_problems.append(
                    f"{key} ← {space_name}: NOT verified — the job result carries no "
                    f"resolved version for it (the engine may predate the value-spaces "
                    f"registry, or the run failed before resolution)."
                )
                continue
            resolved_version = resolution.get("version")
            try:
                space = client.get_value_space(space_name, version=resolved_version)
            except (AethisAPIError, httpx.HTTPError) as e:
                # `{e}`, never `{e.detail}`: `.detail` exists only on
                # AethisAPIError, so formatting it raised AttributeError for
                # every httpx error this clause was widened to catch — and
                # AttributeError is caught by no handler here or at the
                # boundary, so a successful generation ended in a raw
                # traceback. Widening a catch means widening the formatting.
                reason = _error_reason(e)
                ref_problems.append(
                    f"{key} ← {space_name}@v{resolved_version}: NOT verified — could not "
                    f"read the space at that version ({reason})."
                )
                continue
            expected_members = set(space.get("members") or [])
            actual_members = produced_members[key]
            synced_version = sync_state.get(space_name)
            if synced_version is not None and synced_version != resolved_version:
                warn(
                    f"Value space '{space_name}' advanced to v{resolved_version} during "
                    f"generation (this checkout synced against v{synced_version}). Legal — "
                    f"the generation snapshot resolved the newer head — but check the delta."
                )
            if actual_members == expected_members:
                verified_lines.append(
                    f"{key} ← {space_name}@v{resolved_version} ({len(expected_members)} members verified)"
                )
            else:
                member_drift.append(
                    (key, sorted(actual_members - expected_members), sorted(expected_members - actual_members))
                )
            continue
        pinned_members = set(field.get("enum_values") or [])
        if not pinned_members or key not in produced_members:
            continue
        actual_members = produced_members[key]
        if actual_members == pinned_members:
            continue
        member_drift.append((key, sorted(actual_members - pinned_members), sorted(pinned_members - actual_members)))

    for line in verified_lines:
        console.print(f"[dim]{line}[/dim]")
    for problem in ref_problems:
        warn(problem)

    if not missing and not extra and not member_drift and not ref_problems:
        success(f"Fields: all {len(pinned)} pinned field(s) were produced.")
        return

    if missing:
        console.print(f"[yellow]Pinned but not produced:[/yellow] {', '.join(missing)}")
    if extra:
        console.print(f"[yellow]Produced but not pinned:[/yellow] {', '.join(extra)}")
    for key, unpinned, dropped in member_drift:
        detail = []
        if unpinned:
            detail.append(f"added {', '.join(unpinned)}")
        if dropped:
            detail.append(f"dropped {', '.join(dropped)}")
        if not produced_members.get(key):
            detail.append("the produced field declares no members at all")
        console.print(f"[red]Enum members differ from the pin:[/red] {key} — {'; '.join(detail)}")
    if member_drift:
        console.print(
            "[dim]A generated member the spec did not pin can change decisions "
            "no test case covers — check it before publishing.[/dim]"
        )
    console.print("[dim]Run 'aethis fields pull' to sync fields.yaml with what was generated.[/dim]")


class GenerationOutcome(NamedTuple):
    """How a polled generation ended, and what artefact (if any) it produced.

    ``ruleset_id`` is the best available identifier for what this run produced,
    and it is never read back from local state — a diff against a previously
    recorded ruleset reads exactly like one of this run, which is worse than no
    diff at all. It is ``None`` whenever nothing named one, which includes
    every failure today: ``result_ruleset_id`` is written only on the engine's
    success paths, so a failed job usually names no draft. Callers must treat
    ``None`` as "nothing to compare", not as "look it up somewhere else".

    What is *guaranteed* differs by source, so do not read more into it than
    ``_resolved_ruleset_id`` provides: the job's own ``result_ruleset_id`` is
    this run's artefact; the ``latest_ruleset_id`` fallback is the project's
    newest, which under a concurrent generation on the same project may belong
    to another run.
    """

    status: str  # "success" | "failed" | "timeout"
    ruleset_id: Optional[str]
    # Per-field resolved {space, version, space_id} the job stamped at
    # generation start (aethis-core#424) — what makes the drift report's
    # registry-aware verification possible. None from engines that predate it.
    value_spaces_resolved: Optional[dict] = None


def _error_reason(e: Exception) -> str:
    """A readable reason for either error family this module now catches.

    `AethisAPIError` carries `.detail`; httpx errors do not, and some carry an
    empty `str()`. A formatter that assumes either shape fails on the other —
    which is exactly how widening a catch to httpx produced an AttributeError
    on a successful run.

    The API half delegates to `_api_error_message` rather than re-deriving it:
    that helper exists because a value-space conflict's detail is a *dict*, and
    `str()` on it buries the one sentence the author needs under a repr (PR
    #110). A first draft of this function reimplemented the easy half and lost
    both that and the status code.
    """
    if isinstance(e, AethisAPIError):
        return f"HTTP {e.status_code}: {_api_error_message(e)}"
    return str(e) or e.__class__.__name__


def _resolved_ruleset_id(status_payload: dict) -> Optional[str]:
    """The ruleset a generation produced, preferring the job's own record.

    ``result_ruleset_id`` is set on the **job**, so it identifies that run's
    artefact. ``latest_ruleset_id`` is set on the **project**, so it identifies
    whatever was generated most recently by anyone — under two generations
    against one project (aethis-core#420) that is not necessarily this one. The
    fallback is kept because an engine may record no id on the job at all, but
    it is a fallback, not an equivalent.
    """
    job = status_payload.get("job") or {}
    return job.get("result_ruleset_id") or status_payload.get("latest_ruleset_id")


def _invalidate_stale_pointer(project_dir: Path, status: str, *, produced_id: Optional[str] = None) -> None:
    """Stop an unsuccessful run leaving a pointer that reads like its result.

    ``.aethis/state.json``'s ``ruleset_id`` is written only when a generation
    succeeds, and it is what ``aethis fields pull`` (and ``decide`` / ``explain``
    / ``fields``) default to. After an unsuccessful run it therefore still names
    an **earlier** generation, and the next `fields pull` syncs from that one
    with nothing said — the author sees fields appear and reasonably reads them
    as the ones just generated.

    The unsuccessful endings are not the same and are not treated the same.
    The discriminator is whether the run has been **ruled on**:

    - **failed** — the engine has ruled on this run, and the recorded id is
      known not to describe it. The pointer is cleared, so the next `fields
      pull` refuses with "No ruleset_id" instead of quietly using the old
      ruleset. The id is printed so nothing is lost: it can still be passed
      with ``--ruleset-id``.
    - **timeout**, **error** — nothing has been ruled; the job may still be
      running, or may have succeeded while the poll broke, and no other command
      writes this pointer, so clearing it would discard a live reference over a
      client-side clock or a transient 500. The pointer stands and is named
      instead, which is what "not silently" requires.
    """
    state = read_state(project_dir)
    prior = state.get("ruleset_id")
    if not prior:
        return
    if status != "failed" and produced_id is not None and prior == produced_id:
        # This run recorded the pointer, so there is nothing stale to warn
        # about however the run ended afterwards. Without this the guard fires
        # on its own success: a transport failure *after* the id was written
        # made the CLI announce "Done! Ruleset: X" and then insist X was from
        # an earlier generation — a false claim about the exact fact this
        # whole guard exists to keep honest.
        return
    if status != "failed":
        warn(
            f"'.aethis/state.json' still records ruleset {prior} from an earlier generation. "
            f"This run has not recorded one, so 'aethis fields pull' would sync from that "
            f"earlier ruleset rather than from this job."
        )
        # The recovery is one call away and the id needed to make it is on
        # disk, so print the command rather than the situation. `latest_ruleset_id`
        # is a property of the project, which is why this is offered as
        # something to compare rather than written back automatically.
        console.print(
            f"[dim]  If the job in fact finished, 'aethis --output json projects show "
            f"{state.get('project_id') or '<project-id>'}' names it as 'latest_ruleset_id'.[/dim]"
        )
        return
    write_state(project_dir, {"ruleset_id": None})
    warn(
        f"Cleared the recorded ruleset {prior} — it is from an earlier generation, not this "
        f"one, so 'aethis fields pull' will refuse rather than silently sync from it. Pass "
        f"'--ruleset-id {prior}' explicitly if that earlier ruleset is what you want."
    )


# How many CONSECUTIVE transport failures a poll absorbs before reporting the
# API as unreachable.
#
# Deliberately consecutive-only. A total cap was tried and removed: because it
# is absolute rather than rate-relative, its tolerance shrinks as a job gets
# longer (~40% of polls on a 60s job, ~4% on a 600s one) — strictest exactly
# where exposure to blips is greatest. It aborted lossy-but-working runs one
# poll short of the success they were about to receive, which is the #122
# experience this module exists to prevent. What it bought was only a better
# *message* on a flapping link, and not even that much: the timeout ending
# already names the stale pointer, so nothing was silent either way.
_TRANSPORT_BLIP_BUDGET = 3


def _poll_until_done(
    client: AethisClient,
    pid: str,
    project_dir: Path,
    timeout: int = 600,
    *,
    no_publish: bool = False,
) -> GenerationOutcome:
    """Poll a generation to completion and report how it ended.

    Returns rather than raising on failure. Raising here is what made the
    post-generation drift report unreachable in the one case it was written
    for: the exception left the poll loop several frames below the call that
    prints the diff, so a failed generation said "Generation failed" and
    nothing about what the model had actually produced. Exiting is the
    caller's job, after it has reported.

    ``no_publish`` suppresses the publish on success and nothing else. It is
    threaded down to here rather than handled by the caller because this is
    where the publish happens, and a caller that published afterwards would
    reintroduce the activation the flag exists to prevent.
    """
    deadline = time.monotonic() + timeout
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Generating ruleset...", total=100)
        blips = 0
        total_blips = 0  # reported at the deadline, never used to abort
        blip_trace_shown = False
        while time.monotonic() < deadline:
            try:
                result = client.get_status(pid)
            except httpx.HTTPError:
                # A poll runs for minutes, which makes it the command most
                # exposed to a transient network fault and the one where giving
                # up costs most: the job behind it usually finishes, and the
                # caller is then left holding a pointer to an earlier
                # generation. Absorbing a short blip turns the common form of
                # #122 into no incident at all.
                #
                # Bounded, because tolerating indefinitely would report a real
                # outage as a timeout — which reads as a slow job and sends the
                # author looking in the wrong place. Two bounds, because the
                # consecutive one alone cannot see a flapping link.
                blips += 1
                total_blips += 1
                if blips > _TRANSPORT_BLIP_BUDGET:
                    raise
                progress.update(task, description="[yellow]lost the connection — retrying[/yellow]")
                time.sleep(3)
                continue
            if blips and not blip_trace_shown:
                # A durable line, because the transient progress description is
                # overwritten by the next poll and is invisible off a TTY —
                # and an absorbed blip is the event that used to strand a
                # generation, so it should leave a trace worth grepping.
                #
                # Once, not once per recovery: the total cap that used to bound
                # this at 8 is gone, so a flapping 600s poll would otherwise
                # emit ~100 identical lines through the progress bar. The
                # timeout ending reports the running total in one line.
                blip_trace_shown = True
                warn("Lost the connection to the API during the poll; recovered and carried on.")
            blips = 0
            job = result.get("job") or {}
            pct = job.get("progress_percent", 0)
            if isinstance(pct, bool) or not isinstance(pct, (int, float)):
                pct = 0
            job_status = job.get("status", "unknown")
            progress.update(task, completed=pct, description=f"[cyan]{format_poll_description(job)}[/cyan]")

            if job_status == "success":
                progress.update(task, completed=100)
                # The job's own id first: `latest_ruleset_id` is a property of
                # the PROJECT, so under two generations against one project
                # (aethis-core#420) it can name somebody else's artefact. It
                # stays as the fallback because engines that record nothing on
                # the job still have to yield something usable.
                ruleset_id = _resolved_ruleset_id(result)
                # The status payload the source questions are read from: the
                # engine attaches them only once the job names its ruleset, so
                # keep the latest one the re-poll below fetches.
                terminal = result
                # The engine can report success a beat before either id is
                # populated. Re-poll briefly so the state write — and the
                # `fields pull` / field-diff steps that read it — don't miss it.
                for _ in range(5):
                    if ruleset_id:
                        break
                    time.sleep(2)
                    try:
                        terminal = client.get_status(pid)
                        ruleset_id = _resolved_ruleset_id(terminal)
                    except httpx.HTTPError:
                        # Strictly worse than the reported case if it escaped:
                        # the job has already reported success, so raising here
                        # would strand a generation the CLI knows finished. The
                        # blip costs this attempt and nothing else.
                        continue
                # Only record a real id — never clobber a prior good one with None
                # if the engine was slow to surface it.
                if ruleset_id:
                    write_state(project_dir, {"ruleset_id": ruleset_id})
                else:
                    # A success that never surfaced an id leaves the pointer
                    # naming an earlier generation — the same silent shape the
                    # unsuccessful endings are guarded against, arrived at from
                    # the one ending that reads like it worked.
                    _invalidate_stale_pointer(project_dir, "no-id")
                console.print()
                if no_publish:
                    # Publishing ACTIVATES the ruleset, which is the wrong
                    # ending for authoring that must leave a draft behind. Say
                    # the flag's name: this reads much like the publish-failed
                    # message below, and an author needs to tell the ending
                    # they chose from the one that happened to them.
                    if ruleset_id:
                        success(
                            f"Done! Ruleset: {ruleset_id} — left unpublished (--no-publish); "
                            f"run 'aethis publish' to activate it."
                        )
                    else:
                        success(
                            "Done! Ruleset generated and left unpublished (--no-publish) — run "
                            "'aethis status' to get its id, then 'aethis publish' to activate it."
                        )
                    render_source_safeguards(terminal)
                    return GenerationOutcome("success", ruleset_id, job.get("value_spaces_resolved"))
                # Auto-publish so the ruleset is immediately usable
                published = None
                try:
                    published = client.publish(pid)
                    if ruleset_id:
                        success(f"Done! Ruleset published: {ruleset_id}")
                    else:
                        success("Done! Ruleset generated — run 'aethis status' to get its id.")
                except (AethisAPIError, httpx.HTTPError) as e:
                    # Benign for state — the id was recorded above — but not for
                    # the reader: "Could not reach the Aethis API" as the last
                    # word on a successful generation reads as a failed one, and
                    # gets it re-run. The generation is therefore reported as
                    # the success it was — but the reason is named too, or a
                    # green tick is all the author gets for a ruleset that was
                    # never activated, and this ending becomes indistinguishable
                    # from the deliberate --no-publish one above.
                    warn(f"Publish did not run: {e}")
                    if ruleset_id:
                        success(f"Done! Ruleset: {ruleset_id} (run 'aethis publish' to activate)")
                    else:
                        success("Done! Ruleset generated (run 'aethis publish' to activate).")
                # The publish response carries the source check and repeats the
                # questions; print the questions once, from the publish when it
                # answered and from the status otherwise.
                has_questions = isinstance(published, dict) and "source_questions" in published
                render_source_safeguards(published, questions_from=published if has_questions else terminal)
                return GenerationOutcome("success", ruleset_id, job.get("value_spaces_resolved"))

            if job_status == "failed":
                console.print()
                console.print(f"[bold red]Generation failed:[/bold red] {job.get('error_message', 'unknown error')}")
                render_source_safeguards(result)
                # Whatever draft the engine attached to the failed job — today
                # usually nothing, since result_ruleset_id is written only on
                # its success paths. Read it rather than assume: if the engine
                # ever does attach one, the diff below becomes useful for free,
                # and there is no other id that could honestly stand in.
                return GenerationOutcome("failed", job.get("result_ruleset_id"), job.get("value_spaces_resolved"))

            time.sleep(3)

    console.print(
        f"\n[bold red]Timed out after {timeout}s.[/bold red] "
        f"Use 'aethis status -p {pid}' to inspect the server heartbeat and convergence."
    )
    console.print(
        f"[dim]If you intend to abandon a stale run, use 'aethis cancel -p {pid}'. "
        "Cancellation releases the project but may not stop an already-running worker.[/dim]"
    )
    if total_blips:
        # Without this, a flapping link is indistinguishable from a slow job —
        # which sends the author to look at the engine rather than the network.
        warn(
            f"The connection to the API dropped {total_blips} time(s) during this poll, so the "
            f"job may have been progressing normally and the timeout may be a network problem."
        )
    return GenerationOutcome("timeout", None)
