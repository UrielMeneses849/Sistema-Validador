from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.maintenance import Maintenance
from app.models.service_event import ServiceEvent
from app.models.vehicle import Vehicle
from app.services.maintenance_rules import (
    ServiceInterval,
    TOLERANCE_KILOMETERS,
    TOLERANCE_MONTHS,
    USED_VEHICLE_KILOMETER_INTERVAL,
    USED_VEHICLE_MONTH_INTERVAL,
    evaluate_service_interval,
)
from app.services.manufacturer_maintenance_rule_service import get_active_rule_by_brand


@dataclass(frozen=True)
class MaintenanceReference:
    source: Literal["contract_start", "service_event", "maintenance"]
    source_id: int
    service_date: date
    mileage_km: int
    maintenance: Optional[Maintenance] = None
    event: Optional[ServiceEvent] = None


@dataclass(frozen=True)
class AppliedMaintenancePolicy:
    source: Literal["manufacturer", "used_vehicle_default"]
    interval_months: int
    interval_km: int
    brand: Optional[str] = None
    rule_id: Optional[int] = None


@dataclass(frozen=True)
class TimelineAssessment:
    status: str
    validation_state: str
    message: str
    reasons: list[str]
    previous: Optional[MaintenanceReference]
    normal_kilometer_limit: Optional[int]
    normal_date_limit: Optional[date]
    kilometer_limit: Optional[int]
    date_limit: Optional[date]
    delta_km: Optional[int]
    meets_normal_kilometer_condition: Optional[bool]
    meets_normal_time_condition: Optional[bool]
    meets_kilometer_condition: Optional[bool]
    meets_time_condition: Optional[bool]
    vehicle_condition: str
    service_sequence: Optional[Literal["first", "subsequent"]]
    policy: Optional[AppliedMaintenancePolicy]


def _references_for_vehicle(
    db: Session,
    vehicle_id: int,
    current_event_id: int | None,
    event_scope_ids: set[int] | None = None,
) -> list[MaintenanceReference]:
    references: list[MaintenanceReference] = []
    event_query = select(ServiceEvent).where(
        ServiceEvent.vehicle_id == vehicle_id,
        ServiceEvent.service_category == "preventive_maintenance",
        ServiceEvent.resets_maintenance_interval.is_(True),
        ServiceEvent.service_date.is_not(None),
        ServiceEvent.mileage_km.is_not(None),
    )
    if current_event_id is not None:
        event_query = event_query.where(ServiceEvent.id != current_event_id)
    if event_scope_ids is not None:
        event_query = event_query.where(ServiceEvent.id.in_(event_scope_ids))
    for event in db.scalars(event_query):
        assert event.service_date is not None and event.mileage_km is not None
        references.append(
            MaintenanceReference("service_event", event.id, event.service_date, event.mileage_km, event=event)
        )
    maintenances = db.scalars(
        select(Maintenance).where(
            Maintenance.vehicle_id == vehicle_id,
            Maintenance.resets_maintenance_interval.is_(True),
        )
    )
    for maintenance in maintenances:
        references.append(
            MaintenanceReference(
                "maintenance",
                maintenance.id,
                maintenance.maintenance_date,
                maintenance.odometer,
                maintenance=maintenance,
            )
        )
    return references


def find_previous_maintenance(
    db: Session, event: ServiceEvent, event_scope_ids: set[int] | None = None
) -> Optional[MaintenanceReference]:
    if event.service_date is None or event.mileage_km is None:
        return None
    candidates = [
        reference
        for reference in _references_for_vehicle(
            db, event.vehicle_id, event.id, event_scope_ids
        )
        if reference.service_date < event.service_date
        or (reference.service_date == event.service_date and reference.mileage_km < event.mileage_km)
    ]
    return max(candidates, key=lambda item: (item.service_date, item.mileage_km, item.source_id), default=None)


def _contract_reference(vehicle: Vehicle) -> MaintenanceReference | None:
    if vehicle.fecha_inicio_contrato is None or vehicle.initial_odometer is None:
        return None
    return MaintenanceReference(
        source="contract_start",
        source_id=vehicle.id,
        service_date=vehicle.fecha_inicio_contrato,
        mileage_km=vehicle.initial_odometer,
    )


def resolve_maintenance_policy(
    db: Session, vehicle: Vehicle
) -> tuple[AppliedMaintenancePolicy | None, list[str]]:
    if vehicle.vehicle_condition == "used":
        return (
            AppliedMaintenancePolicy(
                source="used_vehicle_default",
                interval_months=USED_VEHICLE_MONTH_INTERVAL,
                interval_km=USED_VEHICLE_KILOMETER_INTERVAL,
            ),
            [],
        )
    if vehicle.vehicle_condition == "new":
        rule = get_active_rule_by_brand(db, vehicle.brand)
        if rule is None:
            return None, [
                "No existe una política de mantenimiento configurada para la marca seleccionada."
            ]
        return (
            AppliedMaintenancePolicy(
                source="manufacturer",
                interval_months=rule.interval_months,
                interval_km=rule.interval_km,
                brand=rule.brand,
                rule_id=rule.id,
            ),
            [],
        )
    return None, [
        "La condición del vehículo no está definida como Nuevo (M1) o Seminuevo (M2)."
    ]


def _assessment(
    *,
    status: str,
    validation_state: str,
    message: str,
    reasons: list[str],
    vehicle_condition: str,
    service_sequence: Literal["first", "subsequent"] | None = None,
    previous: MaintenanceReference | None = None,
    policy: AppliedMaintenancePolicy | None = None,
    interval: ServiceInterval | None = None,
) -> TimelineAssessment:
    return TimelineAssessment(
        status=status,
        validation_state=validation_state,
        message=message,
        reasons=reasons,
        previous=previous,
        normal_kilometer_limit=interval.normal_kilometer_limit if interval else None,
        normal_date_limit=interval.normal_date_limit if interval else None,
        kilometer_limit=interval.kilometer_limit if interval else None,
        date_limit=interval.date_limit if interval else None,
        delta_km=interval.delta_km if interval else None,
        meets_normal_kilometer_condition=(
            interval.meets_normal_kilometer_condition if interval else None
        ),
        meets_normal_time_condition=(
            interval.meets_normal_time_condition if interval else None
        ),
        meets_kilometer_condition=interval.meets_kilometer_condition if interval else None,
        meets_time_condition=interval.meets_time_condition if interval else None,
        vehicle_condition=vehicle_condition,
        service_sequence=service_sequence,
        policy=policy,
    )


def assess_service_event(
    db: Session, event: ServiceEvent, event_scope_ids: set[int] | None = None
) -> TimelineAssessment:
    vehicle = db.get(Vehicle, event.vehicle_id)
    vehicle_condition = vehicle.vehicle_condition if vehicle else "unknown"
    if (
        event.resets_maintenance_interval is not True
        or event.service_category != "preventive_maintenance"
    ):
        return _assessment(
            status="NOT_MAINTENANCE_EVENT",
            validation_state="requires_review",
            message="El evento se conserva en el historial, pero no reinicia el intervalo de mantenimiento.",
            reasons=["No hay evidencia suficiente de mantenimiento preventivo/programado."],
            vehicle_condition=vehicle_condition,
        )
    if event.service_date is None or event.mileage_km is None:
        missing = []
        if event.service_date is None:
            missing.append("Falta la fecha del servicio actual.")
        if event.mileage_km is None:
            missing.append("Falta el kilometraje del servicio actual.")
        return _assessment(
            status="INSUFFICIENT_DATA",
            validation_state="missing_current_data",
            message="Falta fecha o kilometraje del servicio actual.",
            reasons=missing,
            vehicle_condition=vehicle_condition,
        )
    if vehicle is None:
        return _assessment(
            status="REQUIRES_REVIEW",
            validation_state="requires_review",
            message="No fue posible consultar el vehículo del evento.",
            reasons=["Falta el vehículo asociado al evento."],
            vehicle_condition=vehicle_condition,
        )

    previous = find_previous_maintenance(db, event, event_scope_ids)
    service_sequence: Literal["first", "subsequent"] = "subsequent" if previous else "first"
    missing_baseline: list[str] = []
    if previous is None:
        previous = _contract_reference(vehicle)
        if vehicle.fecha_inicio_contrato is None:
            missing_baseline.append("Falta la fecha de inicio del contrato para validar el primer servicio.")
        if vehicle.initial_odometer is None:
            missing_baseline.append("Falta el kilometraje al inicio del contrato para validar el primer servicio.")

    policy, policy_reasons = resolve_maintenance_policy(db, vehicle)
    review_reasons = [*missing_baseline, *policy_reasons]
    if review_reasons:
        message = (
            policy_reasons[0]
            if policy_reasons and not missing_baseline
            else "Faltan datos indispensables para aplicar la regla del mantenimiento."
        )
        return _assessment(
            status="REQUIRES_REVIEW",
            validation_state="requires_review",
            message=message,
            reasons=review_reasons,
            vehicle_condition=vehicle_condition,
            service_sequence=service_sequence,
            previous=previous,
            policy=policy,
        )

    assert previous is not None and policy is not None
    interval = evaluate_service_interval(
        previous_date=previous.service_date,
        previous_odometer=previous.mileage_km,
        current_date=event.service_date,
        current_odometer=event.mileage_km,
        interval_months=policy.interval_months,
        interval_km=policy.interval_km,
    )
    regression_reasons = []
    if event.service_date < previous.service_date:
        regression_reasons.append("La fecha del servicio es anterior a la fecha de referencia.")
    if event.mileage_km < previous.mileage_km:
        regression_reasons.extend(
            [
                "possible_mileage_inconsistency",
                f"Anterior: {previous.mileage_km:,} km; actual: {event.mileage_km:,} km.",
            ]
        )
    if regression_reasons:
        return _assessment(
            status="REQUIRES_REVIEW",
            validation_state="requires_review",
            message="Se detectaron datos regresivos respecto a la referencia; requiere revisión.",
            reasons=regression_reasons,
            vehicle_condition=vehicle_condition,
            service_sequence=service_sequence,
            previous=previous,
            policy=policy,
            interval=interval,
        )

    if interval.is_compliant:
        status = "COMPLIANT"
        message = "El mantenimiento se realizó dentro del intervalo normal aplicable."
        reasons: list[str] = []
    elif interval.is_within_tolerance:
        status = "TOLERANCE_PERIOD"
        message = "El mantenimiento utilizó el periodo de tolerancia permitido."
        dimensions = []
        if not interval.meets_normal_kilometer_condition:
            dimensions.append("kilometraje")
        if not interval.meets_normal_time_condition:
            dimensions.append("tiempo")
        reasons = [
            "Se superó el límite normal de "
            f"{' y '.join(dimensions)}, sin exceder los máximos con tolerancia."
        ]
    elif not interval.meets_kilometer_condition and not interval.meets_time_condition:
        status = "EXCEEDED_BOTH"
        message = "El mantenimiento excedió los límites máximos de kilometraje y tiempo."
        reasons = [
            "Se superó el límite máximo de "
            f"{policy.interval_km + TOLERANCE_KILOMETERS:,} km.",
            "Se superó el límite máximo de "
            f"{policy.interval_months + TOLERANCE_MONTHS} meses calendario.",
        ]
    elif not interval.meets_kilometer_condition:
        status = "EXCEEDED_MILEAGE"
        message = "El mantenimiento excedió el límite máximo de kilometraje."
        reasons = [
            "Se superó el límite máximo de "
            f"{policy.interval_km + TOLERANCE_KILOMETERS:,} km."
        ]
    else:
        status = "EXCEEDED_TIME"
        message = "El mantenimiento excedió el límite máximo de tiempo."
        reasons = [
            "Se superó el límite máximo de "
            f"{policy.interval_months + TOLERANCE_MONTHS} meses calendario."
        ]
    if event.requires_human_review:
        return _assessment(
            status="REQUIRES_REVIEW",
            validation_state="requires_review",
            message=(
                "La fecha y el kilometraje permitieron calcular el intervalo, "
                "pero el evento todavía requiere revisión humana."
            ),
            reasons=event.warnings
            or ["La confianza o clasificación del evento requiere confirmación humana."],
            vehicle_condition=vehicle_condition,
            service_sequence=service_sequence,
            previous=previous,
            policy=policy,
            interval=interval,
        )
    return _assessment(
        status=status,
        validation_state=(
            "compliant"
            if status == "COMPLIANT"
            else "tolerance_period"
            if status == "TOLERANCE_PERIOD"
            else "non_compliant"
        ),
        message=message,
        reasons=reasons,
        vehicle_condition=vehicle_condition,
        service_sequence=service_sequence,
        previous=previous,
        policy=policy,
        interval=interval,
    )
