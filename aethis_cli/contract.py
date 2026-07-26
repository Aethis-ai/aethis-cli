"""The public decision contract, as this CLI consumes it.

One module owns every rule the CLI applies to a ``/decide``, ``/schema`` or
``/explain`` response, so a consumer-side behaviour ("never present a
terminal verdict beside blocking input errors") can be read, tested and
changed in one place instead of being re-implemented per command.

The contract itself is the engine's, published with the API:

**Immutable identity.** A response about a published leaf ruleset always
carries ``ruleset_id`` (the immutable identifier, never the caller's slug),
``ruleset_version`` (the published version label) and ``content_digest``
(``sha256:<hex>`` over the published rule content). A republish always
changes the digest, even when the version label is reused. ``"unknown"`` is
not a legal version for a published leaf; if one arrives the CLI says so
rather than printing it as if it were an identity.

**Blocking input errors.** Every entry in ``field_errors`` is *blocking*: the
named input could not be applied to the ruleset (unknown key, or a value that
does not convert to the field's type). A response carrying a non-empty
``field_errors`` reports ``decision == "undetermined"`` -- the engine never
computes a terminal verdict from a partial input set the caller did not
knowingly send.

The CLI does not *trust* that last invariant, it *enforces* it. A stale
deployment, a proxy replaying a cached body, or a third-party API-compatible
server could all return ``eligible`` beside blocking errors. Presenting that
to a developer as a success -- in human output, in JSON, or merely through a
zero exit status -- is the failure this module exists to make impossible.
When the response contradicts the contract the CLI presents
``undetermined``, records the contradiction under :data:`CONTRACT_NOTE_KEY`,
and exits :data:`EXIT_BLOCKING_INPUT`.

**Supporting sources.** Criteria may carry publish-validated
``source_references``: a versioned DTO naming the cited document, its
authority, licence, the verbatim quoted text, a self-locating deep link, and
the digest of the bytes fetched and verified at publish time. The same DTO is
served by ``GET /rulesets/{id}/explain`` and by ``POST /decide`` with
``include_explanation`` -- nested under ``explanation.groups[].criteria[]``
on the decide path, and directly on ``criteria[]`` on the explain path.
Sources are *supporting evidence*, never the result and never the logic
trace; the renderers keep the three apart.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

# ---------------------------------------------------------------------------
# Exit statuses
# ---------------------------------------------------------------------------

#: Everything the caller asked for succeeded.
EXIT_OK = 0
#: The request failed (transport, auth, 4xx/5xx envelope).
EXIT_ERROR = 1
#: The server answered, but the answer is not actionable: at least one input
#: was rejected, so no terminal verdict exists. Distinct from ``EXIT_ERROR``
#: so a script can tell "fix your inputs" from "the call failed", and distinct
#: from ``EXIT_OK`` so a blocked evaluation can never pass a shell gate.
EXIT_BLOCKING_INPUT = 3

#: Namespaced key under which the CLI records any contract enforcement it had
#: to perform. Absent from the emitted JSON when the response was compliant,
#: so a conforming server's payload passes through unchanged.
CONTRACT_NOTE_KEY = "aethis_cli_contract"

#: Decisions a caller may act on. ``undetermined`` is deliberately excluded.
TERMINAL_DECISIONS = ("eligible", "not_eligible")

#: The version label a published leaf ruleset must never report.
UNRESOLVED_VERSION = "unknown"

#: Top-level members the ``/decide`` request body defines. The API rejects any
#: other top-level key with a 422 rather than silently ignoring it, so the CLI
#: refuses to send one in the first place -- a local error naming the offending
#: key beats a round-trip and a validation envelope.
DECIDE_REQUEST_KEYS = frozenset(
    {
        "ruleset_id",
        "rulebook_id",
        "field_values",
        "exclude_fields",
        "include_trace",
        "include_explanation",
        "include_graph_overlay",
        "include_timing",
        "no_cache",
        "caller_ref",
    }
)


class UnsupportedRequestOption(ValueError):
    """Raised when a caller asks for a ``/decide`` option the API does not define."""


def check_decide_options(options: Mapping[str, Any]) -> None:
    """Reject undeclared ``/decide`` body members before they reach the wire."""
    unknown = sorted(k for k in options if k not in DECIDE_REQUEST_KEYS)
    if unknown:
        raise UnsupportedRequestOption(
            "The /decide API does not define these request options: "
            + ", ".join(unknown)
            + ". Supported options: "
            + ", ".join(sorted(DECIDE_REQUEST_KEYS))
            + "."
        )


# ---------------------------------------------------------------------------
# Blocking input errors
# ---------------------------------------------------------------------------


def blocking_field_errors(response: Mapping[str, Any]) -> Dict[str, str]:
    """Return the response's blocking input errors as ``{field_id: message}``.

    Tolerates every shape a real response has taken: absent, ``null``, an
    empty object, and (defensively) a non-mapping value from a
    contract-breaking server, which is reported as one opaque entry rather
    than being dropped.
    """
    raw = response.get("field_errors") if isinstance(response, Mapping) else None
    if not raw:
        return {}
    if isinstance(raw, Mapping):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        return {f"[{i}]": str(item) for i, item in enumerate(raw)}
    return {"__field_errors__": str(raw)}


def is_blocked(response: Mapping[str, Any]) -> bool:
    """True when the response carries at least one blocking input error."""
    return bool(blocking_field_errors(response))


def presented_decision(response: Mapping[str, Any]) -> str:
    """The only decision the CLI may show for this response.

    Never a terminal verdict while blocking errors are present, whatever the
    server said.
    """
    decision = response.get("decision")
    if is_blocked(response):
        return "undetermined"
    return str(decision) if decision is not None else "unknown"


def contract_violations(response: Mapping[str, Any]) -> List[str]:
    """Ways this response contradicts the published contract.

    Empty for every conforming response. Non-empty means the CLI overrode
    something the server said, and the override is reported rather than
    applied silently.
    """
    violations: List[str] = []
    if not isinstance(response, Mapping):
        return violations

    if is_blocked(response):
        decision = response.get("decision")
        if decision in TERMINAL_DECISIONS:
            violations.append(
                f"server reported decision={decision!r} alongside blocking field_errors; "
                "presented as 'undetermined' (a terminal verdict is not defined "
                "when an input was rejected)"
            )
        explanation = response.get("explanation")
        if isinstance(explanation, Mapping):
            if explanation.get("decision") in TERMINAL_DECISIONS:
                violations.append(
                    f"embedded explanation.decision={explanation['decision']!r} contradicts the "
                    "blocking field_errors; presented as 'undetermined'"
                )
            if explanation.get("decision_path"):
                violations.append(
                    f"embedded explanation.decision_path={explanation['decision_path']!r} claims a "
                    "satisfying path beside blocking field_errors; dropped"
                )
        trace = response.get("trace")
        if isinstance(trace, Mapping):
            if trace.get("status") in TERMINAL_DECISIONS:
                violations.append(
                    f"embedded trace.status={trace['status']!r} contradicts the blocking "
                    "field_errors; presented as 'undetermined'"
                )
            if trace.get("path"):
                violations.append(
                    f"embedded trace.path={trace['path']!r} claims a satisfying path beside "
                    "blocking field_errors; dropped"
                )

    if "ruleset_id" in response and response.get("rulebook_id") is None:
        if response.get("ruleset_version") == UNRESOLVED_VERSION:
            violations.append(
                "ruleset_version is 'unknown' for a ruleset response; the published "
                "version could not be resolved, so this result is not reproducible"
            )

    return violations


def guard_response(response: Any) -> Any:
    """Return a copy of ``response`` safe to emit as JSON.

    A conforming response is returned unchanged. A contradicting one has every
    positive terminal presentation replaced with ``undetermined`` -- top level,
    embedded explanation and embedded trace alike -- and gains a
    :data:`CONTRACT_NOTE_KEY` member recording what was overridden and why, so
    the enforcement is visible to a machine consumer rather than a silent edit.
    """
    if not isinstance(response, Mapping):
        return response

    violations = contract_violations(response)
    blocked = is_blocked(response)
    if not violations and not blocked:
        return response

    guarded: Dict[str, Any] = copy.deepcopy(dict(response))

    if blocked:
        # Parity with the engine's own forcing sweep
        # (aethis-core public decide route): a blocked response must carry no
        # surviving copy of a terminal verdict OR of the path that would have
        # satisfied it -- top-level `decision`, `explanation.decision`,
        # `explanation.decision_path`, `trace.status`, `trace.path`. Scrubbing
        # a strict subset of what the engine scrubs would leave the CLI
        # presenting "Satisfied by: X" under a blocked result.
        if guarded.get("decision") in TERMINAL_DECISIONS:
            guarded["decision"] = "undetermined"
        explanation = guarded.get("explanation")
        if isinstance(explanation, dict):
            if explanation.get("decision") in TERMINAL_DECISIONS:
                explanation["decision"] = "undetermined"
            explanation.pop("decision_path", None)
        trace = guarded.get("trace")
        if isinstance(trace, dict):
            if trace.get("status") in TERMINAL_DECISIONS:
                trace["status"] = "undetermined"
            trace.pop("path", None)

    note: Dict[str, Any] = {
        "blocking_field_errors": sorted(blocking_field_errors(response)),
        "presented_decision": presented_decision(response),
        "exit_code": EXIT_BLOCKING_INPUT if blocked else EXIT_OK,
    }
    if violations:
        note["violations"] = violations
    guarded[CONTRACT_NOTE_KEY] = note
    return guarded


# ---------------------------------------------------------------------------
# Immutable identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """The immutable identity of the rule artefact that produced a response."""

    ruleset_id: Optional[str] = None
    slug: Optional[str] = None
    rulebook_id: Optional[str] = None
    ruleset_version: Optional[str] = None
    content_digest: Optional[str] = None
    engine_version: Optional[str] = None
    decision_id: Optional[str] = None
    inputs_hash: Optional[str] = None
    decision_time: Optional[str] = None
    unresolved: Tuple[str, ...] = field(default=())

    @property
    def is_reproducible(self) -> bool:
        """True when the response pins enough to replay the call later."""
        return bool(self.content_digest) and bool(self.ruleset_version) and self.ruleset_version != UNRESOLVED_VERSION


def _clean(value: Any) -> Optional[str]:
    return str(value) if value not in (None, "") else None


def _looks_like_a_decision(response: Mapping[str, Any]) -> bool:
    """True for a response that purports to be a decision.

    Used to decide whether a *missing* identity is worth complaining about:
    an arbitrary payload has no identity to report, but something claiming to
    be a decision does.
    """
    return any(key in response for key in ("decision", "decision_id", "fields_evaluated"))


def resolved_identity(response: Mapping[str, Any]) -> Identity:
    """Extract the immutable-identity envelope from any decision-surface response."""
    if not isinstance(response, Mapping):
        return Identity(unresolved=("ruleset_id", "ruleset_version", "content_digest"))

    version = _clean(response.get("ruleset_version"))
    digest = _clean(response.get("content_digest"))
    ruleset_id = _clean(response.get("ruleset_id"))

    unresolved: List[str] = []
    rulebook_id = response.get("rulebook_id")
    if ruleset_id and not rulebook_id:
        if version is None or version == UNRESOLVED_VERSION:
            unresolved.append("ruleset_version")
        if digest is None:
            unresolved.append("content_digest")
    elif not rulebook_id and _looks_like_a_decision(response):
        # A decision-shaped response with no artefact identity at all is the
        # least reproducible case there is, and the one most likely to slip
        # through silently -- the earlier gate only fired when an id was
        # already present, so "no identity" printed a bare heading.
        unresolved.append("ruleset_id")
        if version is None or version == UNRESOLVED_VERSION:
            unresolved.append("ruleset_version")
        if digest is None:
            unresolved.append("content_digest")

    return Identity(
        ruleset_id=ruleset_id,
        slug=_clean(response.get("slug")),
        rulebook_id=_clean(rulebook_id),
        ruleset_version=version,
        content_digest=digest,
        engine_version=_clean(response.get("engine_version")),
        decision_id=_clean(response.get("decision_id")),
        inputs_hash=_clean(response.get("inputs_hash")),
        decision_time=_clean(response.get("decision_time")),
        unresolved=tuple(unresolved),
    )


# ---------------------------------------------------------------------------
# Supporting sources
# ---------------------------------------------------------------------------

#: Members a publish-validated ``SourceReference`` always carries.
SOURCE_REFERENCE_REQUIRED = (
    "source_id",
    "title",
    "authority",
    "url",
    "content_digest",
    "licence",
    "verified_at",
    "quote",
    "deep_link",
)


@dataclass(frozen=True)
class CitedCriterion:
    """One criterion and the sources published against it."""

    criterion_id: str
    title: Optional[str]
    group: Optional[str]
    references: Tuple[Dict[str, Any], ...]


def _criterion_sources(criterion: Mapping[str, Any], group: Optional[str]) -> Optional[CitedCriterion]:
    refs = criterion.get("source_references")
    if not isinstance(refs, list) or not refs:
        return None
    return CitedCriterion(
        criterion_id=str(criterion.get("criterion_id") or ""),
        title=_clean(criterion.get("title")),
        group=group,
        references=tuple(r for r in refs if isinstance(r, Mapping)),
    )


def iter_explanation_sources(explanation: Any) -> Iterator[CitedCriterion]:
    """Yield cited criteria from a ``/decide`` explanation.

    The decide path nests criteria under ``explanation.groups[].criteria[]``.
    That is *not* the shape ``/explain`` returns (a flat ``criteria`` array),
    and conflating the two is how a consumer ends up green against a fixture
    it could never have read off the wire.
    """
    if not isinstance(explanation, Mapping):
        return
    for group in explanation.get("groups") or []:
        if not isinstance(group, Mapping):
            continue
        group_name = _clean(group.get("group"))
        for criterion in group.get("criteria") or []:
            if not isinstance(criterion, Mapping):
                continue
            cited = _criterion_sources(criterion, group_name)
            if cited is not None:
                yield cited


def iter_criteria_sources(criteria: Any) -> Iterator[CitedCriterion]:
    """Yield cited criteria from a ``/explain`` response's flat ``criteria`` array."""
    if not isinstance(criteria, list):
        return
    for criterion in criteria:
        if not isinstance(criterion, Mapping):
            continue
        cited = _criterion_sources(criterion, _clean(criterion.get("group")))
        if cited is not None:
            yield cited


def source_reference_gaps(reference: Mapping[str, Any]) -> List[str]:
    """Required members missing from a source reference (empty when complete).

    A published reference is validated server-side, so a gap here means the
    reference reached the CLI degraded -- worth showing rather than rendering a
    confident-looking citation with holes in it.
    """
    if not isinstance(reference, Mapping):
        return list(SOURCE_REFERENCE_REQUIRED)
    return [key for key in SOURCE_REFERENCE_REQUIRED if not reference.get(key)]


def quoted_text(reference: Mapping[str, Any]) -> Optional[str]:
    """The verbatim quoted text of a reference, if it carries one."""
    quote = reference.get("quote") if isinstance(reference, Mapping) else None
    if isinstance(quote, Mapping):
        return _clean(quote.get("exact"))
    return _clean(quote)


__all__ = [
    "CONTRACT_NOTE_KEY",
    "DECIDE_REQUEST_KEYS",
    "EXIT_BLOCKING_INPUT",
    "EXIT_ERROR",
    "EXIT_OK",
    "SOURCE_REFERENCE_REQUIRED",
    "TERMINAL_DECISIONS",
    "UNRESOLVED_VERSION",
    "CitedCriterion",
    "Identity",
    "UnsupportedRequestOption",
    "blocking_field_errors",
    "check_decide_options",
    "contract_violations",
    "guard_response",
    "is_blocked",
    "iter_criteria_sources",
    "iter_explanation_sources",
    "presented_decision",
    "quoted_text",
    "resolved_identity",
    "source_reference_gaps",
]
