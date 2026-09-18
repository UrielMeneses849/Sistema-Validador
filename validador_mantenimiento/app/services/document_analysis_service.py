from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document_analysis import DocumentAnalysis
from app.models.service_event import ServiceEvent
from app.models.validation import Validation
from app.schemas.document_analysis_schema import DocumentAnalysisRead
from app.schemas.service_event_schema import ServiceEventRead
from app.services.document_parser import ParsedDocument, parse_document
from app.services.document_service import get_document_or_raise
from app.services.extraction_service import DocumentExtractor, HybridExtractor
from app.services.service_event_service import apply_vehicle_consistency_review


class DocumentAnalysisReprocessConflict(Exception):
    """No se reemplaza una extracción que ya fue confirmada por una persona."""


def _field_payload(value: Any, method: str, document_id: int) -> dict[str, Any]:
    evidence = value.evidence(method)
    evidence["source_document_id"] = document_id
    return evidence


def _event_read(event: ServiceEvent) -> ServiceEventRead:
    return ServiceEventRead.model_validate(event)


def _analysis_read(analysis: DocumentAnalysis, events: list[ServiceEvent]) -> DocumentAnalysisRead:
    payload = DocumentAnalysisRead.model_validate(analysis)
    payload.service_events = [_event_read(event) for event in events]
    return payload


def _find_duplicate_event(db: Session, event: ServiceEvent) -> ServiceEvent | None:
    if event.service_date is None or event.mileage_km is None:
        return None
    query = select(ServiceEvent).where(
        ServiceEvent.vehicle_id == event.vehicle_id,
        ServiceEvent.service_date == event.service_date,
        ServiceEvent.mileage_km == event.mileage_km,
    )
    if event.repair_order_number:
        query = query.where(ServiceEvent.repair_order_number == event.repair_order_number)
    return db.scalar(query.order_by(ServiceEvent.id).limit(1))


def _create_event(
    *, document_id: int, vehicle_id: int, method: str, parsed_event, document_fields: dict[str, Any]
) -> ServiceEvent:
    evidence = {
        "date": _field_payload(parsed_event.service_date, method, document_id),
        "mileage_km": _field_payload(parsed_event.mileage, method, document_id),
        "repair_order_number": _field_payload(parsed_event.repair_order_number, method, document_id),
        "dealer": _field_payload(parsed_event.dealer, method, document_id),
        "vehicle": document_fields,
        "works": parsed_event.work_evidence,
        "combined_confidence": round(parsed_event.confidence_score, 4),
        "manually_verified": False,
        "base_requires_human_review": parsed_event.requires_human_review,
    }
    return ServiceEvent(
        document_id=document_id,
        vehicle_id=vehicle_id,
        source_page=parsed_event.service_date.source_page or parsed_event.mileage.source_page,
        extraction_method=method,
        service_date=parsed_event.service_date.normalized_value,
        service_date_raw=parsed_event.service_date.raw_value,
        mileage_km=parsed_event.mileage.normalized_value,
        mileage_raw=parsed_event.mileage.raw_value,
        repair_order_number=parsed_event.repair_order_number.normalized_value,
        dealer=parsed_event.dealer.normalized_value,
        service_category=parsed_event.service_category,
        service_type=parsed_event.service_type,
        description=parsed_event.description,
        resets_maintenance_interval=parsed_event.resets_maintenance_interval,
        confidence=parsed_event.confidence,
        requires_human_review=parsed_event.requires_human_review,
        field_evidence=evidence,
        warnings=parsed_event.warnings,
    )


def _discard_automatic_analysis(db: Session, document_id: int, analysis: DocumentAnalysis | None) -> None:
    """Elimina sólo resultados automáticos reemplazables del documento indicado."""
    confirmed_event = db.scalar(
        select(ServiceEvent.id).where(
            ServiceEvent.document_id == document_id,
            ServiceEvent.user_confirmed.is_(True),
        )
    )
    if confirmed_event is not None:
        raise DocumentAnalysisReprocessConflict(
            "El documento tiene un evento confirmado por una persona; no se puede reemplazar automáticamente."
        )

    # Las validaciones de estos eventos automáticos contienen resultados ya obsoletos.
    for validation in db.scalars(select(Validation).where(Validation.document_id == document_id)):
        db.delete(validation)
    for event in db.scalars(select(ServiceEvent).where(ServiceEvent.document_id == document_id)):
        db.delete(event)
    if analysis:
        db.delete(analysis)
    db.flush()


def analyze_document(
    db: Session,
    document_id: int,
    *,
    force: bool = False,
    extractor: DocumentExtractor | None = None,
) -> DocumentAnalysisRead:
    """Extrae el archivo original; `force` sólo reemplaza resultados automáticos no confirmados."""
    document = get_document_or_raise(db, document_id)
    existing = db.scalar(select(DocumentAnalysis).where(DocumentAnalysis.document_id == document.id))
    if existing:
        if not force:
            events = list(
                db.scalars(select(ServiceEvent).where(ServiceEvent.document_id == document.id).order_by(ServiceEvent.id))
            )
            return _analysis_read(existing, events)
        _discard_automatic_analysis(db, document.id, existing)

    extraction = (extractor or HybridExtractor(debug_run_id=str(document.id))).extract(
        document.file_path,
        document.mime_type,
    )
    parsed: ParsedDocument = parse_document(extraction)
    document_fields = {
        name: _field_payload(value, extraction.method, document.id)
        for name, value in parsed.fields.items()
    }
    document_fields["document_generated_at"] = _field_payload(
        parsed.document_generated_at, extraction.method, document.id
    )
    if extraction.metadata:
        document_fields["extraction_metadata"] = extraction.metadata
        if extraction.method in {"ocr", "tesseract"}:
            document_fields["ocr"] = extraction.metadata
    if parsed.layout_debug:
        document_fields["layout_debug"] = parsed.layout_debug
    analysis = DocumentAnalysis(
        document_id=document.id,
        extraction_method=extraction.method,
        extraction_status="completed" if extraction.has_usable_text else "manual_fallback",
        extracted_text=extraction.text or None,
        document_type=parsed.document_type,
        confidence=parsed.confidence,
        requires_human_review=not extraction.has_usable_text or any(
            event.requires_human_review for event in parsed.service_events
        ),
        warnings=parsed.warnings,
        extracted_fields=document_fields,
    )
    db.add(analysis)
    db.flush()

    persisted_events: list[ServiceEvent] = []
    for parsed_event in parsed.service_events:
        event = _create_event(
            document_id=document.id,
            vehicle_id=document.vehicle_id,
            method=extraction.method,
            parsed_event=parsed_event,
            document_fields=document_fields,
        )
        duplicate = _find_duplicate_event(db, event)
        if duplicate:
            analysis.warnings = [
                *analysis.warnings,
                f"Evento duplicado detectado; se reutiliza el evento #{duplicate.id} como referencia.",
            ]
            persisted_events.append(duplicate)
            continue
        db.add(event)
        db.flush()
        persisted_events.append(event)

    apply_vehicle_consistency_review(db, document.vehicle_id)
    db.commit()
    db.refresh(analysis)
    for event in persisted_events:
        db.refresh(event)
    return _analysis_read(analysis, persisted_events)


def get_document_analysis(db: Session, document_id: int) -> DocumentAnalysisRead | None:
    analysis = db.scalar(select(DocumentAnalysis).where(DocumentAnalysis.document_id == document_id))
    if not analysis:
        return None
    events = list(db.scalars(select(ServiceEvent).where(ServiceEvent.document_id == document_id).order_by(ServiceEvent.id)))
    return _analysis_read(analysis, events)
