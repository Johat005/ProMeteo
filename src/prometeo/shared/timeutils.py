"""Reference-month, calendar and forecastMonth arithmetic.

The central rule is C3S's ``c3s_forecastmonth_1_is_init`` convention:
``forecastMonth=1`` is the initialization month, so the target month is
``init + (forecastMonth - 1)``.  All month arithmetic lives here to avoid
off-by-one errors scattered across the codebase.
"""

from __future__ import annotations

import calendar
import numbers
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from prometeo.shared.errors import ConfigurationError

# Strict YYYY-MM parser.  Two digits are required for the month.
_YEAR_MONTH_PATTERN = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})$")

# Temporal conventions understood by derive_target_month().
_SUPPORTED_CONVENTIONS = {"c3s_forecastmonth_1_is_init"}


@dataclass(frozen=True, order=True)
class YearMonth:
    """Immutable year/month value used in requests and file names.

    ``order=True`` makes sorting and comparisons behave like a calendar.
    """

    year: int
    month: int

    def __post_init__(self) -> None:
        # The project only handles modern climate data; reject nonsense years.
        if self.year < 1900:
            raise ValueError("year must be >= 1900")
        # Reject impossible months early.
        if not 1 <= self.month <= 12:
            raise ValueError("month must be between 1 and 12")

    def __str__(self) -> str:
        # Canonical representation used in filenames, CLI output and CPT.
        return f"{self.year:04d}-{self.month:02d}"

    @classmethod
    def parse(cls, value: str) -> "YearMonth":
        """Parse ``YYYY-MM``."""
        return parse_year_month(value)

    def start(self) -> datetime:
        """Return the first day of the month."""
        return datetime(self.year, self.month, 1)


def parse_year_month(value: str) -> YearMonth:
    """Parse ``YYYY-MM`` and raise an actionable error when invalid."""
    # Match the whole string; partial matches such as "2026-11-01" are invalid.
    match = _YEAR_MONTH_PATTERN.match(str(value).strip())
    if match is None:
        raise ConfigurationError(
            f"Periodo inválido: {value!r}",
            hint="Use el formato YYYY-MM, por ejemplo 2026-11.",
        )

    try:
        # YearMonth.__post_init__ validates the numeric ranges.
        return YearMonth(int(match.group("year")), int(match.group("month")))
    except ValueError as exc:
        # Convert the low-level ValueError into a user-facing error.
        raise ConfigurationError(
            f"Periodo inválido: {value!r}",
            hint="Use el formato YYYY-MM, por ejemplo 2026-11.",
        ) from exc


def format_year_month(value: YearMonth) -> str:
    """Return the canonical ``YYYY-MM`` representation."""
    return str(value)


def add_months(value: YearMonth, offset: int) -> YearMonth:
    """Add a signed number of months to a year/month value."""
    # Convert to a single month index, add the offset, convert back.
    total = value.year * 12 + (value.month - 1) + offset
    year, month_index = divmod(total, 12)
    return YearMonth(year, month_index + 1)


def month_length(value: YearMonth) -> int:
    """Return the number of days in the month."""
    # calendar.monthrange handles leap years.
    return calendar.monthrange(value.year, value.month)[1]


def current_reference_month(
    timezone: str = "America/Bogota", override: str | None = None
) -> YearMonth:
    """Return the override or the current month in the timezone.

    The CLI does not expose ``override``; it exists so tests can pin the clock.
    """
    if override is not None:
        return parse_year_month(override)

    try:
        # ZoneInfo validates the IANA name and applies daylight-saving rules.
        zone = ZoneInfo(timezone)
    except Exception as exc:
        raise ConfigurationError(
            f"Zona horaria inválida: {timezone}",
            hint="Use un identificador IANA, por ejemplo 'America/Bogota'.",
        ) from exc

    # Use the configured timezone, not the machine's local timezone.
    now = datetime.now(zone)
    return YearMonth(now.year, now.month)


def derive_target_month(
    init_time: object, forecast_month: int, convention: str
) -> YearMonth:
    """Map a C3S forecastMonth to the target month using catalog rules."""
    # Only the convention documented by C3S is supported in Sprint 1.
    if convention not in _SUPPORTED_CONVENTIONS:
        raise ConfigurationError(
            f"Convención temporal no soportada: {convention}",
            hint=(
                "Convenciones válidas: "
                + ", ".join(sorted(_SUPPORTED_CONVENTIONS))
                + "."
            ),
        )

    # forecastMonth starts at 1 for the initialization month.  Accept any
    # integral type (including numpy integers coming from xarray/NetCDF).
    if not isinstance(forecast_month, numbers.Integral) or forecast_month < 1:
        raise ConfigurationError(
            f"forecastMonth inválido: {forecast_month!r}",
            hint="forecastMonth debe ser un entero mayor o igual a 1.",
        )

    # Normalize the initialization value, then apply the month offset.
    init = _as_year_month(init_time)
    return add_months(init, int(forecast_month) - 1)


def _as_year_month(value: object) -> YearMonth:
    """Convert datetime64, cftime, datetime, string or YearMonth."""
    # Already normalized.
    if isinstance(value, YearMonth):
        return value

    # Strings use the strict parser.
    if isinstance(value, str):
        return parse_year_month(value)

    # datetime, date and cftime objects expose year/month attributes.
    year = getattr(value, "year", None)
    month = getattr(value, "month", None)
    if year is not None and month is not None:
        try:
            return YearMonth(int(year), int(month))
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"No se pudo interpretar el periodo: {value!r}",
                hint="Se esperaba un año y un mes válidos.",
            ) from exc

    # numpy.datetime64 has no year/month attributes, but its string form starts
    # with YYYY-MM.  Slicing avoids importing numpy in the shared layer.
    match = _YEAR_MONTH_PATTERN.match(str(value)[:7])
    if match is not None:
        try:
            return YearMonth(int(match.group("year")), int(match.group("month")))
        except ValueError as exc:
            raise ConfigurationError(
                f"No se pudo interpretar el periodo: {value!r}",
                hint="Se esperaba un valor tipo datetime o 'YYYY-MM'.",
            ) from exc

    # Anything else is a programming error or unsupported input.
    raise ConfigurationError(
        f"No se pudo interpretar el periodo: {value!r}",
        hint="Se esperaba datetime, cftime, numpy.datetime64, 'YYYY-MM' o YearMonth.",
    )


__all__ = [
    "YearMonth",
    "add_months",
    "current_reference_month",
    "derive_target_month",
    "format_year_month",
    "month_length",
    "parse_year_month",
]
