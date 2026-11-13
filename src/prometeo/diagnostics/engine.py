"""Diagnostic execution and conversion gate."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from prometeo.canonical.dataset import CanonicalDataset
from prometeo.catalog.models import ModelSpec
from prometeo.diagnostics.checks import run_canonical_checks, run_file_checks
from prometeo.diagnostics.findings import DiagnosticReport, Finding, error
from prometeo.reader.reader import RawDataset, ReadRequest
from prometeo.shared.errors import DiagnosticError, ProMeteoError


class DiagnosticEngine:
    """Run checks and block conversion when an ERROR is present."""

    def __init__(self, *, force: bool = False) -> None:
        self.force = force

    def run(
        self, checks: Iterable[Callable[[], list[Finding]]]
    ) -> DiagnosticReport:
        """Execute the checks and collect their findings."""
        report = DiagnosticReport()

        for check in checks:
            try:
                report.extend(check())
            except ProMeteoError as exc:
                # Expected failure inside a check: keep the actionable hint.
                report.add(
                    error(
                        "INTERNAL",
                        "diagnostics",
                        str(exc),
                        exc.hint or "Revise la entrada y vuelva a intentarlo.",
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive catch-all.
                # A bug in one check must not hide the report or print a
                # traceback to the user.
                report.add(
                    error(
                        "INTERNAL",
                        "diagnostics",
                        f"Fallo inesperado en un chequeo: {exc}",
                        "Reporte el problema con el archivo de entrada.",
                    )
                )
        return report

    def validate_file(
        self, raw: RawDataset, model: ModelSpec, request: ReadRequest | None = None
    ) -> DiagnosticReport:
        """Validate one raw NetCDF dataset."""
        report = self.run([lambda: run_file_checks(raw, model, request)])
        report.files = [str(path) for path in raw.source_files]
        report.metadata = {
            "kind": "file",
            "model": model.id,
            "centre": model.centre,
            "system": model.system,
            "product_forecast": model.product_forecast,
            "product_hindcast": model.product_hindcast,
        }
        return report

    def validate_canonical(self, canonical: CanonicalDataset) -> DiagnosticReport:
        """Validate one canonical dataset."""
        report = self.run([lambda: run_canonical_checks(canonical)])
        report.metadata = {
            "kind": "canonical",
            "variables": list(canonical.variables),
            "schema_version": canonical.schema_version,
        }
        return report

    def can_convert(self, report: DiagnosticReport) -> bool:
        """Return True when the report has no blocking errors or force is set."""
        return self.force or not report.has_errors()

    def assert_can_convert(self, report: DiagnosticReport) -> None:
        """Raise DiagnosticError when conversion is blocked."""
        if self.can_convert(report):
            return

        errors = report.errors()
        first = errors[0] if errors else None
        hint = (
            f"{first.message} Acción: {first.action}"
            if first is not None
            else "Revise el reporte de diagnóstico."
        )
        raise DiagnosticError(
            f"La validación encontró {len(errors)} error(es) bloqueante(s).",
            hint=hint,
        )


def validate_file(
    raw: RawDataset,
    model: ModelSpec,
    request: ReadRequest | None = None,
    *,
    force: bool = False,
) -> DiagnosticReport:
    """Convenience wrapper around DiagnosticEngine.validate_file."""
    return DiagnosticEngine(force=force).validate_file(raw, model, request)


def validate_canonical(
    canonical: CanonicalDataset, *, force: bool = False
) -> DiagnosticReport:
    """Convenience wrapper around DiagnosticEngine.validate_canonical."""
    return DiagnosticEngine(force=force).validate_canonical(canonical)
