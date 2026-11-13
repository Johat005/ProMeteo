"""Structured logging helpers with basic secret redaction.

Console logs always go to stderr so stdout stays available for machine-readable
results (JSON reports, summaries, dry-run payloads).  Redaction is a second
line of defense: credentials should never be passed to logging in the first
place, but if they are, this module hides them.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from prometeo.shared.io import ensure_directory

# Key-name fragments that indicate a credential.  The comparison is done on
# tokens, so "monkey" does not match "key" but "cdsapi_key" does.
_SECRET_TOKENS = {
    "key",
    "apikey",
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "credential",
    "credentials",
    "cdsapi",
}

# URL userinfo pattern: https://user:secret@host -> https://user:***@host.
_URL_USERINFO = re.compile(r"(?i)(https?://[^/\s:@]+:)([^@\s/]+)(@)")


class _JsonFormatter(logging.Formatter):
    """Render one JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        # Fixed fields make the logs easy to parse and grep.
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include the traceback only when logging an exception.
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # ensure_ascii=False keeps Spanish accents readable.
        return json.dumps(payload, ensure_ascii=False)


def new_run_id() -> str:
    """Return a short identifier for one CLI invocation."""
    # Eight hex characters are enough to correlate logs from one command run.
    return uuid.uuid4().hex[:8]


def configure_logging(
    *,
    level: str = "INFO",
    log_file: Path | None = None,
    json_mode: bool = False,
) -> None:
    """Configure console logging and an optional file handler."""
    # Use a single project logger; child loggers are namespaced below it.
    logger = logging.getLogger("prometeo")
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    # Avoid duplicate lines through the root logger.
    logger.propagate = False

    # Remove and close previous handlers so repeated calls are idempotent.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    # Choose a human-readable or JSON formatter.
    formatter: logging.Formatter
    if json_mode:
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        )

    # Console handler defaults to stderr; stdout stays clean for results.
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    # Optional file handler.  Create its parent directory if necessary.
    if log_file is not None:
        path = Path(log_file)
        ensure_directory(path.parent)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced ProMeteo logger."""
    # Accept both "download" and "prometeo.download" without double-prefixing.
    if name == "prometeo" or name.startswith("prometeo."):
        return logging.getLogger(name)
    return logging.getLogger(f"prometeo.{name}")


def _is_secret_key(key: object) -> bool:
    """Return True when a mapping key looks like a credential name."""
    # Split on non-alphanumeric characters, so cdsapi_key -> ["cdsapi", "key"].
    parts = [part for part in re.split(r"[^a-z0-9]+", str(key).lower()) if part]
    return any(part in _SECRET_TOKENS for part in parts)


def _redact_value(value: Any) -> Any:
    """Recursively redact mappings, sequences and URL userinfo."""
    if isinstance(value, Mapping):
        return redact_secrets(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, str):
        # Mask the password portion of a URL, preserving scheme and host.
        return _URL_USERINFO.sub(r"\1***\3", value)
    return value


def redact_secrets(values: Mapping[str, object]) -> dict[str, object]:
    """Replace credential-like values before logging or serializing."""
    redacted: dict[str, object] = {}
    for key, value in values.items():
        if _is_secret_key(key):
            # The key itself is safe to show; the value is not.
            redacted[key] = "***"
        else:
            redacted[key] = _redact_value(value)
    return redacted
