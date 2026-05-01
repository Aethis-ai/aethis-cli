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


class AuthRequired(Exception):
    """Raised when an authenticated call is attempted without a usable API key.

    Distinguishes "no credentials available, can't even try" from "credentials
    were rejected by the server" (which is ``AuthenticationError`` /
    ``AethisAPIError(status_code=401)``).
    """
