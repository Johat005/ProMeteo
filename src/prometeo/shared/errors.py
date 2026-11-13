"""ProMeteo exception hierarchy and process exit-code mapping.

Every expected, user-facing failure is represented by a subclass of
``ProMeteoError``.  The CLI catches that base class, prints ``format_error()``
and returns ``exit_code_for()``.  Keeping the mapping here means every command
reports failures consistently.
"""

from __future__ import annotations

import re

# Pattern for "key=value" fragments that may contain credentials.  It is used
# by format_error() so that a CDS key cannot leak through an exception message.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|cdsapi[_-]?key|key|token|secret|password|passwd|authorization)"
    r"(\s*[=:]\s*)([^\s,;]+)"
)

# Pattern for URL userinfo such as https://user:secret@host.  The middle group
# is replaced by "***" while preserving the rest of the URL.
_SECRET_URL = re.compile(r"(?i)(https?://[^/\s:@]+:)([^@\s/]+)(@)")


class ProMeteoError(Exception):
    """Base error for expected, user-facing failures.

    ``hint`` is optional.  When present, format_error() renders it as an
    actionable suggestion for the user.
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        # Store the message separately from the hint so the CLI can format the
        # error without relying on string parsing.
        super().__init__(message)
        self.message = message
        self.hint = hint


class ConfigurationError(ProMeteoError):
    """Invalid or missing application configuration."""


class CatalogError(ProMeteoError):
    """Unknown model, variable, lead or invalid catalog data."""


class DownloadError(ProMeteoError):
    """Failure while resolving or retrieving CDS data."""


class CacheError(DownloadError):
    """A cached file is missing or invalid.

    This inherits DownloadError because a bad cache entry normally triggers a
    new download (or, in offline mode, a download failure).
    """


class ReaderError(ProMeteoError):
    """Failure while opening or selecting NetCDF data."""


class DiagnosticError(ProMeteoError):
    """A blocking diagnostic finding or invalid canonical object."""


class CanonicalError(ProMeteoError):
    """The data could not be normalized to the canonical contract."""


class CPTError(ProMeteoError):
    """The CPT document could not be rendered or validated."""


def _redact(text: str) -> str:
    """Hide credential-like values in a free-form string."""
    # First redact "key=..." style assignments, then URL userinfo.  Doing two
    # passes keeps each regular expression simple and predictable.
    text = _SECRET_ASSIGNMENT.sub(r"\1\2***", text)
    return _SECRET_URL.sub(r"\1***\3", text)


def exit_code_for(error: BaseException) -> int:
    """Return 2 for validation/diagnostic failures and 1 otherwise.

    The CLI contract is: 0 = success, 1 = operational/conversion failure,
    2 = validation failure.  DiagnosticError is the only class that maps to 2.
    """
    if isinstance(error, DiagnosticError):
        return 2
    return 1


def format_error(error: BaseException) -> str:
    """Format an error without leaking credentials or tracebacks.

    Expected ProMeteo errors use their message plus an optional Spanish hint.
    Unexpected exceptions fall back to ``str(error)`` (never a traceback).
    """
    if isinstance(error, ProMeteoError):
        # Start with the user-facing message.
        text = error.message
        if error.hint:
            # Add the actionable suggestion on a separate line.
            text = f"{text}\nSugerencia: {error.hint}"
    else:
        # Never show a traceback to the user; show the exception text instead.
        text = str(error) or error.__class__.__name__

    # Apply credential redaction to the final text.
    return _redact(text)
