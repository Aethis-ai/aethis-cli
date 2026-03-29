"""Error types for the Aethis CLI."""

from __future__ import annotations


class AethisAPIError(Exception):
    """Raised when the Aethis API returns a non-2xx response."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class ConfigError(Exception):
    """Raised when aethis.yaml is missing or invalid."""


class AuthenticationError(Exception):
    """Raised when browser-based OAuth authentication fails."""
