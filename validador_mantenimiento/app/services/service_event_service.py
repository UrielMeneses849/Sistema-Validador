from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.service_event import ServiceEvent
from app.schemas.service_event_schema import ManualServiceEventCreate, ServiceEventRead, ServiceEventUpdate
from app.services.document_service import get_document_or_raise


class ServiceEventNotFoundError(Exception):
    pass


def get_service_event_or_raise(db: Session, event_id: int) -> ServiceEvent:
    event = db.get(ServiceEvent, event_id)
    if not event:
        raise ServiceEventNotFoundError(f"No existe el evento de servicio con ID {event_id}.")
    return event


def update_service_event(db: Session, event_id: int, payload: ServiceEventUpdate) -> ServiceEventRead:
    event = get_service_event_or_raise(db, event_id)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(event, field, value)
        if field == "service_date" and value is not None:
            event.service_date_raw = value.isoformat()
        if field == "mileage_km" and value is not None:
            event.mileage_raw = str(value)
    if updates:
        event.user_confirmed = True
        event.requires_human_review = False
        event.confidence = "high"
        event.warnings = []
        db.commit()
        db.refresh(event)
    return ServiceEventRead.model_validate(event)


def create_manual_service_event(db: Session, payload: ManualServiceEventCreate) -> ServiceEventRead:
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
        field_evidence={"source_document_id": document.id, "extraction_method": "manual"},
        warnings=[] if resets and payload.service_date and payload.mileage_km is not None else ["Datos manuales incompletos o sin clasificación."],
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return ServiceEventRead.model_validate(event)
