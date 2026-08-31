from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.maintenance import Maintenance
from app.models.service_event import ServiceEvent
from app.services.maintenance_rules import ServiceInterval, evaluate_service_interval


@dataclass(frozen=True)
class MaintenanceReference:
    source: Literal["service_event", "maintenance"]
    source_id: int
    service_date: date
    mileage_km: int
    maintenance: Optional[Maintenance] = None
    event: Optional[ServiceEvent] = None


@dataclass(frozen=True)
class TimelineAssessment:
    status: str
    message: str
    reasons: list[str]
    previous: Optional[MaintenanceReference]
    kilometer_limit: Optional[int]
    date_limit: Optional[date]
    delta_km: Optional[int]
    meets_kilometer_condition: Optional[bool]
    meets_time_condition: Optional[bool]


def _references_for_vehicle(db: Session, vehicle_id: int, current_event_id: int) -> list[MaintenanceReference]:
    references: list[MaintenanceReference] = []
    events = db.scalars(
        select(ServiceEvent).where(
            ServiceEvent.vehicle_id == vehicle_id,
            ServiceEvent.id != current_event_id,
            ServiceEvent.resets_maintenance_interval.is_(True),
            ServiceEvent.service_date.is_not(None),
            ServiceEvent.mileage_km.is_not(None),
        )
    )
    for event in events:
        assert event.service_date is not None and event.mileage_km is not None
        references.append(
            MaintenanceReference("service_event", event.id, event.service_date, event.mileage_km, event=event)
        )
    maintenances = db.scalars(
        select(Maintenance).where(Maintenance.vehicle_id == vehicle_id)
    )
    for maintenance in maintenances:
        references.append(
            MaintenanceReference(
                "maintenance", maintenance.id, maintenance.maintenance_date, maintenance.odometer, maintenance=maintenance
            )
        )
    return references


def find_previous_maintenance(db: Session, event: ServiceEvent) -> Optional[MaintenanceReference]:
    if event.service_date is None or event.mileage_km is None:
        return None
    candidates = [
        reference
        for reference in _references_for_vehicle(db, event.vehicle_id, event.id)
        if reference.service_date < event.service_date
        or (reference.service_date == event.service_date and reference.mileage_km < event.mileage_km)
    ]
    return max(candidates, key=lambda item: (item.service_date, item.mileage_km, item.source_id), default=None)


def assess_service_event(db: Session, event: ServiceEvent) -> TimelineAssessment:
    if event.resets_maintenance_interval is not True:
        return TimelineAssessment(
            status="NOT_MAINTENANCE_EVENT",
            message="El evento se conserva en el historial, pero no reinicia el intervalo de mantenimiento.",
            reasons=["No hay evidencia suficiente de mantenimiento preventivo/programado."],
            previous=None,
            kilometer_limit=None,
            date_limit=None,
            delta_km=None,
            meets_kilometer_condition=None,
            meets_time_condition=None,
        )
    if event.requires_human_review or event.service_date is None or event.mileage_km is None:
        return TimelineAssessment(
            status="INSUFFICIENT_DATA",
            message="Faltan datos confiables para validar el intervalo; se requiere confirmación humana.",
            reasons=event.warnings or ["Falta fecha o kilometraje de servicio."],
            previous=None,
            kilometer_limit=None,
            date_limit=None,
            delta_km=None,
            meets_kilometer_condition=None,
            meets_time_condition=None,
        )

    previous = find_previous_maintenance(db, event)
    if previous is None:
        return TimelineAssessment(
            status="FIRST_MAINTENANCE_AVAILABLE",
            message="Primer mantenimiento disponible: no existe una evidencia anterior para evaluar el intervalo previo.",
            reasons=["El evento se registró como referencia para validar el siguiente mantenimiento."],
            previous=None,
            kilometer_limit=None,
            date_limit=None,
            delta_km=None,
            meets_kilometer_condition=None,
            meets_time_condition=None,
        )
    if event.mileage_km < previous.mileage_km:
        return TimelineAssessment(
            status="REQUIRES_REVIEW",
            message="Se detectó kilometraje regresivo respecto al mantenimiento anterior; requiere revisión.",
            reasons=[
                "possible_mileage_inconsistency",
                f"Anterior: {previous.mileage_km:,} km; actual: {event.mileage_km:,} km.",
            ],
            previous=previous,
            kilometer_limit=None,
            date_limit=None,
            delta_km=event.mileage_km - previous.mileage_km,
            meets_kilometer_condition=None,
            meets_time_condition=None,
        )

    interval: ServiceInterval = evaluate_service_interval(
        previous_date=previous.service_date,
        previous_odometer=previous.mileage_km,
        current_date=event.service_date,
        current_odometer=event.mileage_km,
    )
    if interval.is_compliant:
        status = "COMPLIANT"
        message = "El mantenimiento se realizó dentro de los límites de 10,000 km y 6 meses."
        reasons: list[str] = []
    elif not interval.meets_kilometer_condition and not interval.meets_time_condition:
        status = "EXCEEDED_BOTH"
        message = "El mantenimiento excedió los límites de kilometraje y tiempo."
        reasons = ["Se superó el límite de 10,000 km.", "Se superó el límite de 6 meses calendario."]
    elif not interval.meets_kilometer_condition:
        status = "EXCEEDED_MILEAGE"
        message = "El mantenimiento excedió el límite de 10,000 km."
        reasons = ["Se superó el límite de kilometraje antes del siguiente mantenimiento."]
    else:
        status = "EXCEEDED_TIME"
        message = "El mantenimiento excedió el límite de 6 meses calendario."
        reasons = ["Se superó el límite de tiempo antes del siguiente mantenimiento."]
    return TimelineAssessment(
        status=status,
        message=message,
        reasons=reasons,
        previous=previous,
        kilometer_limit=interval.kilometer_limit,
        date_limit=interval.date_limit,
        delta_km=interval.delta_km,
        meets_kilometer_condition=interval.meets_kilometer_condition,
        meets_time_condition=interval.meets_time_condition,
    )
