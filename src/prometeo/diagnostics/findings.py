"""Finding, severity and diagnostic report value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np


class Severity(str, Enum):
    """Severity assigned to one diagnostic finding."""

    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


def _jsonable(value: Any) -> Any:
    """Convert numpy/path values into JSON-serializable objects."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, np.datetime64):
        return str(value.astype("datetime64[D]"))
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [_jsonable(item) for item in value.tolist()]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


@dataclass(frozen=True)
class Finding:
    """One diagnostic observation with an actionable message."""

    check_id: str
    severity: Severity
    location: str
    message: str
    action: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation."""
        return {
            "check_id": self.check_id,
            "severity": self.severity.value,
            "location": self.location,
            "message": self.message,
            "action": self.action,
            "context": {key: _jsonable(value) for key, value in self.context.items()},
        }

    def is_blocking(self) -> bool:
        """Return whether the finding blocks conversion."""
        return self.severity is Severity.ERROR


@dataclass
class DiagnosticReport:
    """Collection of findings produced by one validation run."""

    findings: list[Finding] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add(self, finding: Finding) -> None:
        """Append one finding to the report."""
        self.findings.append(finding)

    def extend(self, findings: list[Finding]) -> None:
        """Append several findings to the report."""
        self.findings.extend(findings)

    def errors(self) -> list[Finding]:
        """Return the findings with ERROR severity."""
        return [
            finding for finding in self.findings if finding.severity is Severity.ERROR
        ]

    def warnings(self) -> list[Finding]:
        """Return the findings with WARNING severity."""
        return [
            finding for finding in self.findings if finding.severity is Severity.WARNING
        ]

    def info(self) -> list[Finding]:
        """Return the findings with INFO severity."""
        return [
            finding for finding in self.findings if finding.severity is Severity.INFO
        ]

    def has_errors(self) -> bool:
        """Return True when at least one finding is blocking."""
        return bool(self.errors())

    def exit_code(self) -> int:
        """Return 2 when there are errors, otherwise 0."""
        return 2 if self.has_errors() else 0

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation."""
        return {
            "files": list(self.files),
            "metadata": {key: _jsonable(value) for key, value in self.metadata.items()},
            "findings": [finding.to_dict() for finding in self.findings],
            "counts": {
                "errors": len(self.errors()),
                "warnings": len(self.warnings()),
                "info": len(self.info()),
            },
            "exit_code": self.exit_code(),
        }


def error(
    check_id: str, location: str, message: str, action: str, **context: Any
) -> Finding:
    """Build an ERROR finding."""
    return Finding(
        check_id=check_id,
        severity=Severity.ERROR,
        location=location,
        message=message,
        action=action,
        context=dict(context),
    )


def warning(
    check_id: str, location: str, message: str, action: str, **context: Any
) -> Finding:
    """Build a WARNING finding."""
    return Finding(
        check_id=check_id,
        severity=Severity.WARNING,
        location=location,
        message=message,
        action=action,
        context=dict(context),
    )


def info(
    check_id: str, location: str, message: str, action: str, **context: Any
) -> Finding:
    """Build an INFO finding."""
    return Finding(
        check_id=check_id,
        severity=Severity.INFO,
        location=location,
        message=message,
        action=action,
        context=dict(context),
    )
