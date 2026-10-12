from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.service_event import ServiceEvent
from app.models.validation import Validation
from app.schemas.service_event_schema import ManualServiceEventCreate, ServiceEventRead, ServiceEventUpdate
from app.services.document_service import get_document_or_raise
from app.services.training_dataset_service import record_human_verified_event


class ServiceEventNotFoundError(Exception):
    pass


CONSISTENCY_WARNING = "El kilometraje disminuye respecto al servicio cronológicamente anterior."


def get_service_event_or_raise(db: Session, event_id: int) -> ServiceEvent:
    event = db.get(ServiceEvent, event_id)
    if not event:
        raise ServiceEventNotFoundError(f"No existe el evento de servicio con ID {event_id}.")
    return event


def apply_vehicle_consistency_review(
    db: Session, vehicle_id: int, event_ids: set[int] | None = None
) -> None:
    """Marca regresiones, nunca cambia fecha/km ni usa coherencia para inventar lecturas."""
    query = select(ServiceEvent).where(ServiceEvent.vehicle_id == vehicle_id)
    if event_ids is not None:
        query = query.where(ServiceEvent.id.in_(event_ids))
    events = list(
        db.scalars(
            query.order_by(
                ServiceEvent.service_date.is_(None),
                ServiceEvent.service_date,
                ServiceEvent.mileage_km,
                ServiceEvent.id,
            )
        )
    )
    previous_mileage: int | None = None
    for event in events:
        evidence = dict(event.field_evidence or {})
        base_review = bool(evidence.get("base_requires_human_review", event.requires_human_review))
        warnings = [warning for warning in list(event.warnings or []) if warning != CONSISTENCY_WARNING]
        regression = (
            event.service_date is not None
            and event.mileage_km is not None
            and previous_mileage is not None
            and event.mileage_km < previous_mileage
        )
        if regression:
            warnings.append(CONSISTENCY_WARNING)
        event.requires_human_review = base_review or regression
        event.warnings = warnings
        evidence["consistency_review"] = {
            "mileage_regression": regression,
            "previous_mileage_km": previous_mileage,
        }
        event.field_evidence = evidence
        if event.service_date is not None and event.mileage_km is not None:
            previous_mileage = event.mileage_km


def _recalculate_vehicle_history(db: Session, vehicle_id: int) -> None:
    """Reemplaza dictámenes derivados del vehículo tras una confirmación humana."""
    event_ids = list(
        db.scalars(
            select(ServiceEvent.id)
            .where(ServiceEvent.vehicle_id == vehicle_id)
            .order_by(
                ServiceEvent.service_date.is_(None),
                ServiceEvent.service_date,
                ServiceEvent.mileage_km,
                ServiceEvent.id,
            )
        )
    )
    event_id_set = set(event_ids)
    # Sólo se reemplazan dictámenes derivados de ServiceEvent. Las validaciones
    # manuales/legadas del vehículo mantienen intacta su trazabilidad.
    validations = list(db.scalars(select(Validation).where(Validation.vehicle_id == vehicle_id)))
    for validation in validations:
        details = validation.analysis_details or {}
        if details.get("service_event_id") in event_id_set:
            db.delete(validation)
    db.commit()
    # Import local para conservar el desacoplamiento y evitar un ciclo de módulos.
    from app.services.validation_service import validate_service_event

    for current_id in event_ids:
        validate_service_event(db, current_id)


def update_service_event(db: Session, event_id: int, payload: ServiceEventUpdate) -> ServiceEventRead:
    event = get_service_event_or_raise(db, event_id)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(event, field, value)
        if field == "service_date":
            event.service_date_raw = value.isoformat() if value is not None else None
        if field == "mileage_km":
            event.mileage_raw = str(value) if value is not None else None
    if updates:
        event.user_confirmed = True
        fields_complete = event.service_date is not None and event.mileage_km is not None
        event.requires_human_review = not fields_complete
        event.confidence = "high" if fields_complete else "low"
        evidence = dict(event.field_evidence or {})
        evidence["manually_verified"] = True
        evidence["combined_confidence"] = 1.0
        evidence["base_requires_human_review"] = not fields_complete
        evidence["manual_correction"] = {
            "service_date": event.service_date.isoformat() if event.service_date else None,
            "mileage_km": event.mileage_km,
        }
        for evidence_name in ("date", "mileage_km"):
            if isinstance(evidence.get(evidence_name), dict):
                corrected_value = (
                    event.service_date.isoformat()
                    if evidence_name == "date" and event.service_date
                    else event.mileage_km
                    if evidence_name == "mileage_km"
                    else None
                )
                original_candidates = evidence[evidence_name].get("candidates", [])
                candidates = [
                    {**candidate, "selected": False}
                    for candidate in original_candidates
                    if isinstance(candidate, dict)
                ]
                candidates.append({
                    "value": corrected_value,
                    "rawText": str(corrected_value) if corrected_value is not None else None,
                    "label": "Corrección manual",
                    "page": event.source_page,
                    "boundingBox": None,
                    "score": 1.0,
                    "reasons": ["Valor confirmado y guardado por una persona antes de revalidar."],
                    "selected": True,
                })
                evidence[evidence_name] = {
                    **evidence[evidence_name],
                    "raw_value": str(corrected_value) if corrected_value is not None else None,
                    "normalized_value": corrected_value,
                    "manually_verified": True,
                    "confidence": "high",
                    "confidence_score": 1.0,
                    "ambiguous": False,
                    "candidates": candidates,
                }
        event.field_evidence = evidence
        event.warnings = [] if fields_complete else ["La corrección manual todavía tiene campos incompletos."]
        db.flush()
        apply_vehicle_consistency_review(db, event.vehicle_id)
        db.commit()
        db.refresh(event)
        record_human_verified_event(event)
        _recalculate_vehicle_history(db, event.vehicle_id)
        db.refresh(event)
    return ServiceEventRead.model_validate(event)


def confirm_service_event(db: Session, event_id: int) -> ServiceEventRead:
    """Confirma ambos valores visibles sin obligar al usuario a alterarlos."""
    event = get_service_event_or_raise(db, event_id)
    return update_service_event(
        db,
        event_id,
        ServiceEventUpdate(service_date=event.service_date, mileage_km=event.mileage_km),
    )


def create_manual_service_event(
    db: Session, payload: ManualServiceEventCreate, *, persist: bool = True
) -> ServiceEventRead:
    """Fallback de corrección humana que conserva el mismo modelo/timeline."""
    document: Document = get_document_or_raise(db, payload.document_id)
    text = f"{payload.service_type or ''} {payload.description or ''}".upper()
    strong_maintenance_evidence = any(
        token in text for token in ("MANTENIMIENTO", "PREVENTIVO", "CAMBIO DE ACEITE", "FILTRO DE ACEITE", "SERVICIO")
    )
    resets = payload.resets_maintenance_interval
    if resets is None:
        resets = True if strong_maintenance_evidence else None
    category = "preventive_maintenance" if resets else "unknown"
    event = ServiceEvent(
        document_id=document.id,
        vehicle_id=document.vehicle_id,
        extraction_method="manual",
        service_date=payload.service_date,
        service_date_raw=payload.service_date.isoformat() if payload.service_date else None,
        mileage_km=payload.mileage_km,
        mileage_raw=str(payload.mileage_km) if payload.mileage_km is not None else None,
        repair_order_number=payload.repair_order_number,
        dealer=payload.dealer,
        service_category=category,
        service_type=payload.service_type,
        description=payload.description,
        resets_maintenance_interval=resets,
        confidence="high",
        requires_human_review=not bool(resets) or payload.service_date is None or payload.mileage_km is None,
        user_confirmed=True,
        field_evidence={
            "source_document_id": document.id,
            "extraction_method": "manual",
            "base_requires_human_review": not bool(resets) or payload.service_date is None or payload.mileage_km is None,
        },
        warnings=[] if resets and payload.service_date and payload.mileage_km is not None else ["Datos manuales incompletos o sin clasificación."],
    )
    db.add(event)
    if persist:
        db.commit()
    else:
        db.flush()
    db.refresh(event)
    return ServiceEventRead.model_validate(event)
