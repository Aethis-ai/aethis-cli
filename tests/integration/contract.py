"""Load the machine-readable public-API contract and validate error envelopes.

The contract (default scopes, per-endpoint scope map, and the exact shape of
the 401/403/429 error envelopes) is served anonymously at
``GET /api/v1/public/diagnostics/contract`` on staging once aethis-core P1
deploys. Until then, a local checkout of the same file is a valid source, so
this loader accepts:

- an ``http(s)://`` URL — fetched over the network (the CI default);
- a ``file://`` URL or a bare filesystem path — read from disk (local dev,
  pointed at the aethis-core checkout).

Strictness: in CI (``CI`` truthy, or ``AETHIS_CONTRACT_STRICT=1``) an
unreachable contract is a hard failure — Decision 9 forbids skip-green. Local
runs without strictness get a ``ContractUnavailable`` the caller turns into a
skip, so a developer whose staging endpoint isn't live yet isn't blocked.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_CONTRACT_URL = os.environ.get(
    "AETHIS_CONTRACT_URL",
    "https://staging.api.aethis.ai/api/v1/public/diagnostics/contract",
)

_HTTP_TIMEOUT = 20.0


class ContractUnavailable(RuntimeError):
    """The contract could not be loaded from the configured source."""


def is_strict() -> bool:
    """True when an unreachable contract must fail the run rather than skip."""
    return bool(os.environ.get("CI")) or os.environ.get("AETHIS_CONTRACT_STRICT") == "1"


def load_contract(url: str = DEFAULT_CONTRACT_URL) -> dict[str, Any]:
    """Return the contract document from ``url`` (http(s), file://, or a path)."""
    parsed = urlparse(url)
    try:
        if parsed.scheme in ("http", "https"):
            resp = httpx.get(url, timeout=_HTTP_TIMEOUT)
            if resp.status_code != 200:
                raise ContractUnavailable(f"contract endpoint returned HTTP {resp.status_code} ({url})")
            return resp.json()
        # file:// or a bare path
        path = Path(parsed.path if parsed.scheme == "file" else url)
        if not path.exists():
            raise ContractUnavailable(f"contract file not found: {path}")
        return json.loads(path.read_text())
    except httpx.HTTPError as exc:
        raise ContractUnavailable(f"contract fetch failed: {exc} ({url})") from exc
    except (ValueError, OSError) as exc:
        raise ContractUnavailable(f"contract parse failed: {exc} ({url})") from exc


def _check(condition: bool, message: str) -> list[str]:
    return [] if condition else [message]


def envelope_violations(detail: Any, envelope_schema: dict[str, Any]) -> list[str]:
    """Return a list of ways ``detail`` violates the contract's error envelope.

    A deliberately small structural validator (the contract's envelope schemas
    are flat objects): it checks the wrapping ``detail`` key, every required
    property, and the ``const`` pins (``error``/``reason_code`` values). An
    empty list means the payload conforms.
    """
    problems: list[str] = []

    detail_schema = envelope_schema.get("properties", {}).get("detail", {})
    props = detail_schema.get("properties", {})
    required = detail_schema.get("required", [])

    if not isinstance(detail, dict):
        return [f"detail is {type(detail).__name__}, expected object"]

    for field in required:
        problems += _check(field in detail, f"missing required field {field!r}")

    for field, spec in props.items():
        if field not in detail:
            continue
        const = spec.get("const")
        if const is not None:
            problems += _check(
                detail.get(field) == const,
                f"{field!r} = {detail.get(field)!r}, contract pins {const!r}",
            )
        enum = spec.get("enum")
        if enum is not None:
            problems += _check(
                detail.get(field) in enum,
                f"{field!r} = {detail.get(field)!r}, not in contract enum {enum}",
            )
        if spec.get("type") == "array" and "minItems" in spec:
            value = detail.get(field)
            problems += _check(
                isinstance(value, list) and len(value) >= spec["minItems"],
                f"{field!r} must have >= {spec['minItems']} items, got {value!r}",
            )

    return problems
