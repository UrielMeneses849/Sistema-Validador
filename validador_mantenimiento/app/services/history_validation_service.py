from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.service_event import ServiceEvent
from app.schemas.history_validation_schema import (
    HistoryBaselineRead,
    HistoryDocumentDetailRead,
    HistoryPolicyRead,
    HistorySummaryRead,
    HistoryValidationRead,
    HistoryValidationRequest,
    HistoryValidationRowRead,
)
from app.schemas.service_event_schema import ServiceEventRead
from app.services.maintenance_rules import add_calendar_months
from app.services.service_event_service import get_service_event_or_raise
from app.services.timeline_service import resolve_maintenance_policy
from app.services.validation_service import (
    preview_service_event_validation,
    validate_service_event,
)
from app.services.vehicle_service import get_vehicle_or_raise


class HistoryVehicleMismatchError(Exception):
    pass


def _calendar_elapsed(start: date | None, end: date | None) -> str | None:
    if start is None or end is None or end < start:
        return None
    months = (end.year - start.year) * 12 + end.month - start.month
    anniversary = add_calendar_months(start, months)
    if anniversary > end:
        months -= 1
        anniversary = add_calendar_months(start, months)
    years, remaining_months = divmod(months, 12)
    days = (end - anniversary).days
    parts: list[str] = []
    if years:
        parts.append(f"{years} {'año' if years == 1 else 'años'}")
    if remaining_months:
        parts.append(
            f"{remaining_months} {'mes' if remaining_months == 1 else 'meses'}"
        )
    if days or not parts:
        parts.append(f"{days} {'día' if days == 1 else 'días'}")
    return ", ".join(parts)


def _document_detail(event: ServiceEvent) -> HistoryDocumentDetailRead:
    document = event.document
    analysis = document.analysis
    return HistoryDocumentDetailRead(
        id=document.id,
        original_filename=document.original_filename,
        document_type=analysis.document_type if analysis else None,
        extraction_method=analysis.extraction_method if analysis else event.extraction_method,
        confidence=analysis.confidence if analysis else event.confidence,
        requires_human_review=analysis.requires_human_review if analysis else event.requires_human_review,
        warnings=list(analysis.warnings or []) if analysis else list(event.warnings or []),
        extracted_fields=dict(analysis.extracted_fields or {}) if analysis else {},
        extracted_text=analysis.extracted_text if analysis else None,
    )


def _summary(rows: list[HistoryValidationRowRead]) -> HistorySummaryRead:
    statuses = [row.validation.status for row in rows]
    compliant = statuses.count("COMPLIANT")
    tolerance_period = statuses.count("TOLERANCE_PERIOD")
    non_compliant = sum(status.startswith("EXCEEDED_") for status in statuses)
    requires_review = len(statuses) - compliant - tolerance_period - non_compliant

    if requires_review:
        result = "REQUIRES_REVIEW"
        message = (
            f"{requires_review} servicio"
            f"{'s requieren' if requires_review != 1 else ' requiere'} revisión humana."
        )
    elif non_compliant:
        result = "NON_COMPLIANT"
        if "EXCEEDED_BOTH" in statuses:
            message = "Se detectó un servicio que excedió los límites de tiempo y kilometraje."
        elif "EXCEEDED_MILEAGE" in statuses:
            message = "Se detectó un servicio que excedió el intervalo permitido por kilometraje."
        else:
            message = "Se detectó un servicio que excedió el intervalo permitido por tiempo."
    elif tolerance_period:
        result = "TOLERANCE_PERIOD"
        message = (
            f"{tolerance_period} servicio"
            f"{'s utilizaron' if tolerance_period != 1 else ' utilizó'} el periodo de tolerancia."
        )
    else:
        result = "COMPLIANT"
        message = "Todos los servicios analizados se encuentran dentro de la política aplicable."
    return HistorySummaryRead(
        services_analyzed=len(rows),
        compliant=compliant,
        tolerance_period=tolerance_period,
        non_compliant=non_compliant,
        requires_review=requires_review,
        result=result,
        message=message,
    )


def validate_maintenance_history(
    db: Session,
    request: HistoryValidationRequest,
    *,
    persist_validations: bool = True,
    include_vehicle_history: bool = True,
) -> HistoryValidationRead:
    """Valida una secuencia completa cuando todos sus eventos ya están persistidos."""
    vehicle = get_vehicle_or_raise(db, request.vehicle_id)
    selected_events: list[ServiceEvent] = []
    for event_id in request.event_ids:
        event = get_service_event_or_raise(db, event_id)
        if event.vehicle_id != vehicle.id:
            raise HistoryVehicleMismatchError(
                f"El evento #{event.id} no pertenece al vehículo seleccionado."
            )
        selected_events.append(event)

    # El resultado representa el historial completo ya conocido del vehículo,
    # no sólo los archivos agregados en la interacción actual. Los eventos
    # recién seleccionados ya fueron verificados arriba y todos están
    # persistidos antes de comenzar a validar la secuencia.
    events = (
        list(
            db.scalars(select(ServiceEvent).where(ServiceEvent.vehicle_id == vehicle.id))
        )
        if include_vehicle_history
        else selected_events
    )

    events.sort(
        key=lambda event: (
            event.service_date is None,
            event.service_date or date.max,
            event.mileage_km is None,
            event.mileage_km if event.mileage_km is not None else 0,
            event.id,
        )
    )

    # These two columns describe the chronological history shown to the user,
    # not the maintenance-policy reference selected by VAL-002. A document
    # can require review (or not reset the maintenance interval) and still be
    # the immediately preceding row in the visible timeline.
    previous_history_date = vehicle.fecha_inicio_contrato
    previous_history_mileage = vehicle.initial_odometer
    event_scope_ids = {event.id for event in events}
    rows: list[HistoryValidationRowRead] = []
    for event in events:
        validation = (
            validate_service_event(db, event.id)
            if persist_validations
            else preview_service_event_validation(
                db, event, event_scope_ids=event_scope_ids
            )
        )
        elapsed_time = _calendar_elapsed(previous_history_date, event.service_date)
        delta_km = (
            event.mileage_km - previous_history_mileage
            if event.mileage_km is not None and previous_history_mileage is not None
            else None
        )
        rows.append(
            HistoryValidationRowRead(
                service_event=ServiceEventRead.model_validate(event),
                validation=validation,
                elapsed_time=elapsed_time,
                delta_km=delta_km,
                document=_document_detail(event),
            )
        )
        if event.service_date is not None:
            previous_history_date = event.service_date
        if event.mileage_km is not None:
            previous_history_mileage = event.mileage_km

    policy, policy_reasons = resolve_maintenance_policy(db, vehicle)
    policy_read = (
        HistoryPolicyRead(
            source=policy.source,
            brand=policy.brand,
            months=policy.interval_months,
            kilometers=policy.interval_km,
            rule_id=policy.rule_id,
        )
        if policy
        else None
    )
    return HistoryValidationRead(
        vehicle_id=vehicle.id,
        vehicle_condition=vehicle.vehicle_condition,
        ephemeral=not persist_validations,
        baseline=HistoryBaselineRead(
            date=vehicle.fecha_inicio_contrato,
            mileage_km=vehicle.initial_odometer,
        ),
        policy=policy_read,
        policy_reasons=policy_reasons,
        rows=rows,
        summary=_summary(rows),
    )
