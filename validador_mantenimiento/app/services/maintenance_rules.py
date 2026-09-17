from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date


USED_VEHICLE_KILOMETER_INTERVAL = 10_000
USED_VEHICLE_MONTH_INTERVAL = 6
TOLERANCE_KILOMETERS = 1_000
TOLERANCE_MONTHS = 1

# Alias conservados para consumidores existentes.
KILOMETER_INTERVAL = USED_VEHICLE_KILOMETER_INTERVAL
MONTH_INTERVAL = USED_VEHICLE_MONTH_INTERVAL


@dataclass(frozen=True)
class ServiceInterval:
    """Resultado de aplicar los límites normal y máximo a un servicio."""

    normal_kilometer_limit: int
    normal_date_limit: date
    kilometer_limit: int
    date_limit: date
    delta_km: int
    meets_normal_kilometer_condition: bool
    meets_normal_time_condition: bool
    meets_kilometer_condition: bool
    meets_time_condition: bool

    @property
    def is_compliant(self) -> bool:
        return (
            self.meets_normal_kilometer_condition
            and self.meets_normal_time_condition
        )

    @property
    def is_within_tolerance(self) -> bool:
        return (
            not self.is_compliant
            and self.meets_kilometer_condition
            and self.meets_time_condition
        )


# Nombre anterior conservado para compatibilidad de imports; comparte exactamente
# la misma semántica: True significa que el límite máximo no fue excedido.
MaintenanceWindow = ServiceInterval


def add_calendar_months(origin: date, months: int) -> date:
    """Suma meses de calendario respetando el último día del mes cuando hace falta."""
    absolute_month = origin.month - 1 + months
    year = origin.year + absolute_month // 12
    month = absolute_month % 12 + 1
    day = min(origin.day, monthrange(year, month)[1])
    return date(year, month, day)


def evaluate_service_interval(
    *,
    previous_date: date,
    previous_odometer: int,
    current_date: date,
    current_odometer: int,
    interval_months: int = USED_VEHICLE_MONTH_INTERVAL,
    interval_km: int = USED_VEHICLE_KILOMETER_INTERVAL,
) -> ServiceInterval:
    """Evalúa el intervalo normal y la tolerancia global, según lo que ocurra primero."""
    if interval_months <= 0 or interval_km <= 0:
        raise ValueError("Los intervalos de mantenimiento deben ser mayores que cero.")
    normal_kilometer_limit = previous_odometer + interval_km
    normal_date_limit = add_calendar_months(previous_date, interval_months)
    kilometer_limit = normal_kilometer_limit + TOLERANCE_KILOMETERS
    date_limit = add_calendar_months(
        previous_date, interval_months + TOLERANCE_MONTHS
    )
    return ServiceInterval(
        normal_kilometer_limit=normal_kilometer_limit,
        normal_date_limit=normal_date_limit,
        kilometer_limit=kilometer_limit,
        date_limit=date_limit,
        delta_km=current_odometer - previous_odometer,
        meets_normal_kilometer_condition=current_odometer
        <= normal_kilometer_limit,
        meets_normal_time_condition=current_date <= normal_date_limit,
        meets_kilometer_condition=current_odometer <= kilometer_limit,
        meets_time_condition=current_date <= date_limit,
    )


def evaluate_maintenance_window(
    *,
    last_maintenance_date: date,
    last_maintenance_odometer: int,
    document_date: date,
    document_odometer: int,
    interval_months: int = USED_VEHICLE_MONTH_INTERVAL,
    interval_km: int = USED_VEHICLE_KILOMETER_INTERVAL,
) -> MaintenanceWindow:
    """Adaptador del contrato anterior hacia la única semántica de límites máximos."""
    return evaluate_service_interval(
        previous_date=last_maintenance_date,
        previous_odometer=last_maintenance_odometer,
        current_date=document_date,
        current_odometer=document_odometer,
        interval_months=interval_months,
        interval_km=interval_km,
    )
