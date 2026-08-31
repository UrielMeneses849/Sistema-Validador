from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date


KILOMETER_INTERVAL = 10_000
MONTH_INTERVAL = 6


@dataclass(frozen=True)
class MaintenanceWindow:
    kilometer_limit: int
    date_limit: date
    meets_kilometer_condition: bool
    meets_time_condition: bool

    @property
    def is_due(self) -> bool:
        return self.meets_kilometer_condition or self.meets_time_condition


@dataclass(frozen=True)
class ServiceInterval:
    """Resultado de comparar dos mantenimientos programados consecutivos.

    A diferencia de ``MaintenanceWindow`` (conservado para compatibilidad de
    la Fase 1), aquí ``True`` significa que el intervalo *cumple* el límite.
    """

    kilometer_limit: int
    date_limit: date
    delta_km: int
    meets_kilometer_condition: bool
    meets_time_condition: bool

    @property
    def is_compliant(self) -> bool:
        return self.meets_kilometer_condition and self.meets_time_condition


def add_calendar_months(origin: date, months: int) -> date:
    """Suma meses de calendario respetando el último día del mes cuando hace falta."""
    absolute_month = origin.month - 1 + months
    year = origin.year + absolute_month // 12
    month = absolute_month % 12 + 1
    day = min(origin.day, monthrange(year, month)[1])
    return date(year, month, day)


def evaluate_maintenance_window(
    *,
    last_maintenance_date: date,
    last_maintenance_odometer: int,
    document_date: date,
    document_odometer: int,
) -> MaintenanceWindow:
    kilometer_limit = last_maintenance_odometer + KILOMETER_INTERVAL
    date_limit = add_calendar_months(last_maintenance_date, MONTH_INTERVAL)
    return MaintenanceWindow(
        kilometer_limit=kilometer_limit,
        date_limit=date_limit,
        meets_kilometer_condition=document_odometer >= kilometer_limit,
        meets_time_condition=document_date >= date_limit,
    )


def evaluate_service_interval(
    *,
    previous_date: date,
    previous_odometer: int,
    current_date: date,
    current_odometer: int,
) -> ServiceInterval:
    """Valida la regla de la aseguradora: no exceder km ni fecha límite."""
    kilometer_limit = previous_odometer + KILOMETER_INTERVAL
    date_limit = add_calendar_months(previous_date, MONTH_INTERVAL)
    return ServiceInterval(
        kilometer_limit=kilometer_limit,
        date_limit=date_limit,
        delta_km=current_odometer - previous_odometer,
        meets_kilometer_condition=current_odometer <= kilometer_limit,
        meets_time_condition=current_date <= date_limit,
    )
