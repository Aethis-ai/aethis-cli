"""Pluggable auth providers for ``AethisClient``.

Each profile carries an ``auth_mode`` (default ``"api_key"``) that selects how
the client builds its outbound request headers. A provider is a callable that
takes a :class:`ProviderContext` and returns the headers to attach.

Built-in modes:

* ``api_key`` — ``X-API-Key: <api_key>`` (current default behaviour).
* ``none`` — anonymous (no auth header). Equivalent to ``unsigned=True``.

Plugins (e.g. ``aethis-cli-internal``) register additional modes at startup
via :func:`register_provider`. The registry is process-local; the public
package ships no Cloud Run URLs or gcloud logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class ProviderContext:
    """Inputs available to an auth provider when building request headers.

    ``base_url`` is the already-resolved API host; providers that mint
    audience-scoped tokens (e.g. GCP ID tokens for IAM-gated Cloud Run) should
    use ``profile.get("audience") or base_url``.
    """

    api_key: Optional[str]
    base_url: str
    profile: dict


AuthProvider = Callable[[ProviderContext], dict[str, str]]


class UnknownAuthMode(ValueError):
    """Raised when a profile selects an ``auth_mode`` that no plugin registered."""


_PROVIDERS: dict[str, AuthProvider] = {}


def register_provider(name: str, provider: AuthProvider) -> None:
    """Register an auth provider under ``name``.

    Re-registration replaces the previous provider — plugins can override
    built-ins if they need to (rare).
    """
    _PROVIDERS[name] = provider


def get_provider(name: str) -> AuthProvider:
    """Return the provider registered under ``name`` or raise :class:`UnknownAuthMode`.

    The error message lists the modes that *are* available, which is what a
    misconfigured profile most needs to see.
    """
    try:
        return _PROVIDERS[name]
    except KeyError:
        known = ", ".join(sorted(_PROVIDERS)) or "(none registered)"
        raise UnknownAuthMode(
            f"Unknown auth_mode '{name}'. Available modes: {known}. "
            "If this is a staff mode (e.g. 'gcloud_id_token'), install "
            "aethis-cli-internal."
        )


def known_modes() -> list[str]:
    """Return the names of every currently-registered provider."""
    return sorted(_PROVIDERS)


# -- Built-in providers ------------------------------------------------------


def _api_key_provider(ctx: ProviderContext) -> dict[str, str]:
    if not ctx.api_key:
        return {}
    return {"X-API-Key": ctx.api_key}


def _none_provider(ctx: ProviderContext) -> dict[str, str]:
    return {}


register_provider("api_key", _api_key_provider)
register_provider("none", _none_provider)
