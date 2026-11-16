"""Structural validation for generated CPT+ v10+ files.

The validator is intentionally independent from the writer: it parses the
generated text and checks the format CPT+ v10 actually needs.  That way a writer
bug cannot silently produce a file that looks valid to the writer itself.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from prometeo.shared.errors import CPTError
from prometeo.shared.timeutils import parse_year_month

NAMESPACE = "xmlns:cpt=http://iri.columbia.edu/CPT/v10/"
TAG_PATTERN = re.compile(r"cpt:([A-Za-z0-9_]+)=([^,]+)")
_PERIOD_PATTERN = re.compile(r"^\d{4}-\d{2}(/\d{4}-\d{2})?$")
_REQUIRED_TAGS = {"T", "nrow", "ncol", "units", "missing"}


@dataclass(frozen=True)
class CPTIssue:
    """One structural problem found in a CPT document."""

    code: str
    line: int
    message: str
    action: str


@dataclass
class CPTValidationReport:
    """Result of structurally validating a CPT document."""

    source: str
    issues: list[CPTIssue] = field(default_factory=list)
    blocks: int = 0
    nfields: int | None = None

    @property
    def valid(self) -> bool:
        """Return True when no issues were found."""
        return not self.issues

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation."""
        return {
            "source": self.source,
            "valid": self.valid,
            "blocks": self.blocks,
            "nfields": self.nfields,
            "issues": [
                {
                    "code": issue.code,
                    "line": issue.line,
                    "message": issue.message,
                    "action": issue.action,
                }
                for issue in self.issues
            ],
        }


def _add_issue(
    issues: list[CPTIssue],
    *,
    code: str,
    line: int,
    message: str,
    action: str,
) -> None:
    """Append one issue with a consistent shape."""
    issues.append(
        CPTIssue(code=code, line=line, message=message, action=action)
    )


def _valid_period(value: str) -> bool:
    """Return True for a valid YYYY-MM or YYYY-MM/YYYY-MM period."""
    if not _PERIOD_PATTERN.match(value):
        return False
    try:
        if "/" in value:
            start, end = value.split("/", 1)
            parse_year_month(start)
            parse_year_month(end)
        else:
            parse_year_month(value)
    except Exception:
        return False
    return True


def _parse_numeric_tokens(
    line: str, *, expected: int, line_number: int, issues: list[CPTIssue]
) -> list[float] | None:
    """Split and parse one numeric data line, reporting format issues."""
    tokens = line.split()
    if len(tokens) != expected:
        _add_issue(
            issues,
            code="width",
            line=line_number,
            message=f"Se esperaban {expected} valores y se encontraron {len(tokens)}.",
            action="Revise el número de filas/columnas y la línea de longitud.",
        )
        # Still try to parse what is present so the report can mention numeric
        # problems too, but return None to stop block validation.
        return None

    values: list[float] = []
    for token in tokens:
        try:
            value = float(token)
        except ValueError:
            _add_issue(
                issues,
                code="numeric",
                line=line_number,
                message=f"Valor no numérico: {token!r}.",
                action="Todos los valores deben ser números o -999.",
            )
            return None
        if not math.isfinite(value):
            _add_issue(
                issues,
                code="numeric",
                line=line_number,
                message=f"Valor no finito: {token!r}.",
                action="Use -999 para datos faltantes.",
            )
            return None
        values.append(value)
    return values


def validate_text(text: str, *, source: str = "<string>") -> CPTValidationReport:
    """Validate header, tags, block count and row widths."""
    report = CPTValidationReport(source=source)
    lines = text.splitlines()

    if not lines:
        _add_issue(
            report.issues,
            code="empty",
            line=1,
            message="El documento CPT está vacío.",
            action="Genere el archivo de nuevo.",
        )
        return report

    # Exactly one namespace line.
    if lines[0].strip() != NAMESPACE:
        _add_issue(
            report.issues,
            code="namespace",
            line=1,
            message="La primera línea no es el namespace CPT v10 esperado.",
            action=f"Use exactamente: {NAMESPACE}",
        )

    # Exactly one nfields declaration.
    if len(lines) < 2:
        _add_issue(
            report.issues,
            code="nfields",
            line=2,
            message="Falta la línea cpt:nfields.",
            action="Agregue cpt:nfields=1 como segunda línea.",
        )
        return report

    nfields_match = re.fullmatch(r"cpt:nfields=(\d+)", lines[1].strip())
    if nfields_match is None:
        _add_issue(
            report.issues,
            code="nfields",
            line=2,
            message="La segunda línea debe ser cpt:nfields=N.",
            action="Use cpt:nfields=1 en Sprint 1.",
        )
    else:
        report.nfields = int(nfields_match.group(1))
        if report.nfields != 1:
            _add_issue(
                report.issues,
                code="nfields",
                line=2,
                message=f"Sprint 1 solo admite un campo por archivo (nfields={report.nfields}).",
                action="Use cpt:nfields=1.",
            )

    # Third line: cpt:T followed by every target period, in block order.
    if len(lines) < 3:
        _add_issue(
            report.issues,
            code="date-list",
            line=3,
            message="Falta la línea cpt:T con la lista de fechas.",
            action="Agregue cpt:T <fecha1> <fecha2> ... después de cpt:nfields.",
        )
        return report

    date_tokens = lines[2].strip().split()
    if not date_tokens or date_tokens[0] != "cpt:T":
        _add_issue(
            report.issues,
            code="date-list",
            line=3,
            message="La tercera línea debe empezar por cpt:T.",
            action="Agregue cpt:T <fecha1> <fecha2> ... después de cpt:nfields.",
        )
        return report

    expected_periods = date_tokens[1:]
    if not expected_periods:
        _add_issue(
            report.issues,
            code="date-list",
            line=3,
            message="La línea cpt:T no contiene fechas.",
            action="Incluya al menos una fecha en formato YYYY-MM.",
        )
        return report

    for period in expected_periods:
        if not _valid_period(period):
            _add_issue(
                report.issues,
                code="date-list-period",
                line=3,
                message=f"Fecha inválida en cpt:T: {period!r}.",
                action="Use YYYY-MM o YYYY-MM/YYYY-MM.",
            )

    if len(set(expected_periods)) != len(expected_periods):
        _add_issue(
            report.issues,
            code="date-list-duplicate",
            line=3,
            message="La línea cpt:T contiene fechas duplicadas.",
            action="Cada fecha debe aparecer una sola vez.",
        )

    # Parse blocks.  Each block starts with a line containing cpt:T.
    seen_periods: set[str] = set()
    index = 3
    while index < len(lines):
        header = lines[index].strip()
        if not header:
            index += 1
            continue

        if "cpt:T=" not in header:
            _add_issue(
                report.issues,
                code="block",
                line=index + 1,
                message="Se esperaba una cabecera de bloque con cpt:T.",
                action="Cada bloque debe comenzar con cpt:field=..., cpt:T=..., etc.",
            )
            index += 1
            continue

        tags = {
            key.strip(): value.strip()
            for key, value in TAG_PATTERN.findall(header)
        }

        missing_tags = sorted(_REQUIRED_TAGS - set(tags))
        if missing_tags:
            _add_issue(
                report.issues,
                code="tags",
                line=index + 1,
                message=f"Faltan etiquetas obligatorias: {', '.join(missing_tags)}.",
                action="Incluya cpt:T, cpt:nrow, cpt:ncol, cpt:units y cpt:missing.",
            )
            # Without nrow/ncol the block cannot be parsed safely.
            break

        period = tags["T"]
        block_index = report.blocks
        if block_index < len(expected_periods):
            expected_period = expected_periods[block_index]
            if period != expected_period:
                _add_issue(
                    report.issues,
                    code="date-list-mismatch",
                    line=index + 1,
                    message=(
                        f"cpt:T={period} no coincide con la fecha "
                        f"{expected_period} de la línea cpt:T."
                    ),
                    action=(
                        "Mantenga el mismo orden en la línea cpt:T y en los bloques."
                    ),
                )
        if not _valid_period(period):
            _add_issue(
                report.issues,
                code="period",
                line=index + 1,
                message=f"Periodo CPT inválido: {period!r}.",
                action="Use YYYY-MM o YYYY-MM/YYYY-MM.",
            )
        if period in seen_periods:
            _add_issue(
                report.issues,
                code="duplicate",
                line=index + 1,
                message=f"Periodo CPT duplicado: {period}.",
                action="Cada bloque debe tener un cpt:T único.",
            )
        seen_periods.add(period)

        if tags.get("missing") != "-999":
            _add_issue(
                report.issues,
                code="missing",
                line=index + 1,
                message=f"cpt:missing debe ser -999, no {tags.get('missing')!r}.",
                action="Use cpt:missing=-999.",
            )
        if not tags.get("units"):
            _add_issue(
                report.issues,
                code="units",
                line=index + 1,
                message="cpt:units está vacío.",
                action="Indique las unidades CPT del campo.",
            )

        try:
            nrow = int(tags["nrow"])
            ncol = int(tags["ncol"])
        except ValueError:
            _add_issue(
                report.issues,
                code="shape",
                line=index + 1,
                message="cpt:nrow y cpt:ncol deben ser enteros.",
                action="Revise la cabecera del bloque.",
            )
            break

        if nrow <= 0 or ncol <= 0:
            _add_issue(
                report.issues,
                code="shape",
                line=index + 1,
                message=f"nrow/ncol deben ser positivos: nrow={nrow}, ncol={ncol}.",
                action="Revise la cabecera del bloque.",
            )
            break

        index += 1  # move to the longitude line

        # Longitude line: exactly ncol values.
        if index >= len(lines):
            _add_issue(
                report.issues,
                code="truncated",
                line=index,
                message="Falta la línea de longitudes.",
                action="Cada bloque debe incluir ncol longitudes.",
            )
            break
        if _parse_numeric_tokens(
            lines[index],
            expected=ncol,
            line_number=index + 1,
            issues=report.issues,
        ) is None:
            break
        index += 1

        # Data rows: latitude + ncol values, repeated nrow times.
        block_ok = True
        for _ in range(nrow):
            if index >= len(lines):
                _add_issue(
                    report.issues,
                    code="truncated",
                    line=index,
                    message="Faltan filas de datos del bloque.",
                    action="Cada bloque debe incluir nrow filas.",
                )
                block_ok = False
                break
            if _parse_numeric_tokens(
                lines[index],
                expected=ncol + 1,
                line_number=index + 1,
                issues=report.issues,
            ) is None:
                block_ok = False
                break
            index += 1

        if not block_ok:
            break

        report.blocks += 1

    if report.blocks != len(expected_periods):
        _add_issue(
            report.issues,
            code="date-count",
            line=3,
            message=(
                f"La línea cpt:T declara {len(expected_periods)} fecha(s) "
                f"pero se encontraron {report.blocks} bloque(s)."
            ),
            action="Asegúrese de que cada fecha tenga su bloque de datos.",
        )

    return report


def validate_cpt(path: Path) -> CPTValidationReport:
    """Validate a CPT file on disk."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CPTError(
            f"No se encontró el archivo CPT: {source}",
            hint="Verifique la ruta de salida.",
        ) from exc
    except OSError as exc:
        raise CPTError(
            f"No se pudo leer el archivo CPT: {source}",
            hint=str(exc),
        ) from exc

    return validate_text(text, source=str(source))
