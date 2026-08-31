from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import ALLOWED_EXTENSIONS, ALLOWED_MIME_TYPES
from app.models.audit_log import AuditLog
from app.models.maintenance import Maintenance
from app.models.service_event import ServiceEvent
from app.models.validation import Validation
from app.schemas.validation_schema import AuditLogRead, ValidationRead, ValidationRequest
from app.services.audit_service import register_audit_event
from app.services.document_service import DocumentNotFoundError, get_document_or_raise, is_document_available
from app.services.maintenance_rules import MaintenanceWindow, evaluate_maintenance_window
from app.services.maintenance_service import get_latest_maintenance
from app.services.service_event_service import get_service_event_or_raise
from app.services.timeline_service import TimelineAssessment, assess_service_event
from app.services.vehicle_service import VehicleNotFoundError, get_vehicle_or_raise


STATUS_APPROVED = "APROBADO"
STATUS_OUTSIDE_WINDOW = "FUERA_DE_VENTANA"
STATUS_REJECTED = "RECHAZADO"
STATUS_REVIEW_REQUIRED = "REVISION_REQUERIDA"


def _validation_read(validation: Validation) -> ValidationRead:
    maintenance = validation.maintenance
    details = validation.analysis_details or {}
    previous = details.get("previous_maintenance") or {}
    return ValidationRead(
        id=validation.id,
        validation_code=validation.validation_code,
        vehicle_id=validation.vehicle_id,
        vehicle_internal_number=validation.vehicle.internal_number if validation.vehicle else None,
        document_id=validation.document_id,
        maintenance_id=validation.maintenance_id,
        status=validation.status,
        document_date=validation.document_date,
        document_odometer=validation.document_odometer,
        last_maintenance_date=maintenance.maintenance_date if maintenance else previous.get("service_date"),
        last_maintenance_odometer=maintenance.odometer if maintenance else previous.get("mileage_km"),
        kilometer_limit=validation.kilometer_limit,
        date_limit=validation.date_limit,
        meets_kilometer_condition=validation.meets_kilometer_condition,
        meets_time_condition=validation.meets_time_condition,
        message=validation.message,
        reasons=validation.reasons or [],
        analysis_details=validation.analysis_details,
        created_at=validation.created_at,
        audit_logs=[AuditLogRead.model_validate(event) for event in validation.audit_logs],
    )


def _save_validation(
    db: Session,
    *,
    request: ValidationRequest,
    maintenance: Optional[Maintenance],
    status: str,
    message: str,
    reasons: list[str],
    kilometer_limit: Optional[int],
    date_limit: Optional[date],
    meets_kilometer_condition: Optional[bool],
    meets_time_condition: Optional[bool],
    events: list[tuple[str, str]],
) -> ValidationRead:
    validation = Validation(
        validation_code=f"PENDING-{uuid4().hex}",
        vehicle_id=request.vehicle_id,
        document_id=request.document_id,
        maintenance_id=maintenance.id if maintenance else None,
        document_date=request.document_date,
        document_odometer=request.document_odometer,
        maintenance_type=request.maintenance_type,
        service_description=request.service_description,
        provider_name=request.provider_name,
        invoice_number=request.invoice_number,
        status=status,
        kilometer_limit=kilometer_limit,
        date_limit=date_limit,
        meets_kilometer_condition=meets_kilometer_condition,
        meets_time_condition=meets_time_condition,
        message=message,
        reasons=reasons,
    )
    db.add(validation)
    db.flush()
    validation.validation_code = f"VAL-{validation.id:06d}"
    for event_type, event_message in events:
        register_audit_event(db, validation.id, event_type, event_message)
    db.commit()

    saved = db.scalar(
        select(Validation)
        .options(
            selectinload(Validation.vehicle),
            selectinload(Validation.maintenance),
            selectinload(Validation.audit_logs),
        )
        .where(Validation.id == validation.id)
    )
    assert saved is not None
    return _validation_read(saved)


def _save_service_event_validation(
    db: Session, event: ServiceEvent, assessment: TimelineAssessment
) -> ValidationRead:
    previous = assessment.previous
    previous_payload = (
        {
            "source": previous.source,
            "source_id": previous.source_id,
            "service_date": previous.service_date.isoformat(),
            "mileage_km": previous.mileage_km,
        }
        if previous
        else None
    )
    interval_validation = (
        "not_applicable"
        if assessment.status == "FIRST_MAINTENANCE_AVAILABLE"
        else "requires_review"
        if assessment.status in {"REQUIRES_REVIEW", "INSUFFICIENT_DATA"}
        else "compliant"
        if assessment.status == "COMPLIANT"
        else "not_compliant"
    )
    validation = Validation(
        validation_code=f"PENDING-{uuid4().hex}",
        vehicle_id=event.vehicle_id,
        document_id=event.document_id,
        maintenance_id=previous.maintenance.id if previous and previous.maintenance else None,
        document_date=event.service_date,
        document_odometer=event.mileage_km,
        maintenance_type=event.service_type,
        service_description=event.description,
        provider_name=event.dealer,
        invoice_number=event.repair_order_number,
        status=assessment.status,
        kilometer_limit=assessment.kilometer_limit,
        date_limit=assessment.date_limit,
        meets_kilometer_condition=assessment.meets_kilometer_condition,
        meets_time_condition=assessment.meets_time_condition,
        message=assessment.message,
        reasons=assessment.reasons,
        analysis_details={
            "service_event_id": event.id,
            "document_type": event.document.analysis.document_type if event.document.analysis else None,
            "previous_maintenance": previous_payload,
            "delta_km": assessment.delta_km,
            "interval_validation": interval_validation,
            "extraction_method": event.extraction_method,
        },
    )
    db.add(validation)
    db.flush()
    validation.validation_code = f"VAL-{validation.id:06d}"
    register_audit_event(db, validation.id, "DOCUMENT_ANALYZED", f"Evento #{event.id} obtenido por {event.extraction_method}.")
    register_audit_event(
        db,
        validation.id,
        "EVENT_CLASSIFIED",
        f"Clasificación: {event.service_category}; reinicia intervalo: {event.resets_maintenance_interval}.",
    )
    if previous:
        register_audit_event(
            db,
            validation.id,
            "PREVIOUS_MAINTENANCE",
            f"Referencia {previous.source} #{previous.source_id}: {previous.service_date:%d/%m/%Y}, {previous.mileage_km:,} km.",
        )
    else:
        register_audit_event(db, validation.id, "PREVIOUS_MAINTENANCE", "No se encontró un mantenimiento anterior disponible.")
    if assessment.kilometer_limit is not None and assessment.date_limit is not None:
        register_audit_event(
            db,
            validation.id,
            "INTERVAL_LIMITS",
            f"Límites: {assessment.kilometer_limit:,} km y {assessment.date_limit:%d/%m/%Y}.",
        )
    register_audit_event(db, validation.id, "FINAL_RESULT", f"RESULTADO FINAL: {assessment.status}.")
    db.commit()
    saved = db.scalar(
        select(Validation)
        .options(
            selectinload(Validation.vehicle),
            selectinload(Validation.maintenance),
            selectinload(Validation.audit_logs),
        )
        .where(Validation.id == validation.id)
    )
    assert saved is not None
    return _validation_read(saved)


def validate_service_event(db: Session, event_id: int) -> ValidationRead:
    """Valida un evento automático con la línea de tiempo de su vehículo."""
    event = get_service_event_or_raise(db, event_id)
    assessment = assess_service_event(db, event)
    return _save_service_event_validation(db, event, assessment)


def _file_is_valid(document_path: str, mime_type: str, document_available: bool) -> bool:
    extension = document_path.rsplit(".", 1)[-1].lower() if "." in document_path else ""
    return document_available and f".{extension}" in ALLOWED_EXTENSIONS and mime_type in ALLOWED_MIME_TYPES


def validate_document(db: Session, request: ValidationRequest) -> ValidationRead:
    """Ejecuta y persiste la validación determinista con sus eventos de auditoría."""
    try:
        vehicle = get_vehicle_or_raise(db, request.vehicle_id)
    except VehicleNotFoundError:
        raise
    try:
        document = get_document_or_raise(db, request.document_id)
    except DocumentNotFoundError:
        raise

    events: list[tuple[str, str]] = [("DOCUMENT_RECEIVED", "Documento recibido para validación.")]
    reasons: list[str] = []

    if document.vehicle_id != vehicle.id:
        reasons.append("El documento no pertenece al vehículo seleccionado.")
    if not _file_is_valid(document.original_filename, document.mime_type, is_document_available(document)):
        reasons.append("El archivo original no existe, está vacío o su formato no es válido.")
    else:
        events.append(("FILE_VALID", "Formato y archivo original validados correctamente."))

    if request.document_date is None:
        reasons.append("Falta la fecha que aparece en el documento.")
    elif request.document_date > date.today():
        reasons.append("La fecha del documento no puede ser futura.")
    else:
        events.append(("DOCUMENT_DATE", f"Fecha registrada: {request.document_date:%d/%m/%Y}."))

    if request.document_odometer is None:
        reasons.append("Falta el odómetro que aparece en el documento.")
    elif request.document_odometer < 0:
        reasons.append("El kilometraje no puede ser negativo.")
    else:
        events.append(("DOCUMENT_ODOMETER", f"Odómetro registrado: {request.document_odometer:,} km."))

    if reasons:
        events.append(("FINAL_RESULT", "RESULTADO FINAL: RECHAZADO."))
        return _save_validation(
            db,
            request=request,
            maintenance=None,
            status=STATUS_REJECTED,
            message="La validación fue rechazada porque faltan datos o existen datos no válidos.",
            reasons=reasons,
            kilometer_limit=None,
            date_limit=None,
            meets_kilometer_condition=None,
            meets_time_condition=None,
            events=events,
        )

    last_maintenance = get_latest_maintenance(db, vehicle.id)
    if last_maintenance is None:
        events.append(("REFERENCE_MISSING", "No se encontró un último mantenimiento registrado."))
        events.append(("FINAL_RESULT", "RESULTADO FINAL: REVISION_REQUERIDA."))
        return _save_validation(
            db,
            request=request,
            maintenance=None,
            status=STATUS_REVIEW_REQUIRED,
            message="No es posible calcular la ventana porque el vehículo no tiene un último mantenimiento registrado.",
            reasons=["Falta el mantenimiento de referencia."],
            kilometer_limit=None,
            date_limit=None,
            meets_kilometer_condition=None,
            meets_time_condition=None,
            events=events,
        )

    events.append(("REFERENCE_FOUND", "Último mantenimiento encontrado."))
    coherence_reasons: list[str] = []
    if request.document_date < last_maintenance.maintenance_date:
        coherence_reasons.append("La fecha del documento es anterior a la del último mantenimiento.")
    if request.document_odometer < last_maintenance.odometer:
        coherence_reasons.append("El odómetro del documento es menor al odómetro registrado en el último mantenimiento.")
    if coherence_reasons:
        events.append(("COHERENCE_ERROR", "Se detectaron inconsistencias con el último mantenimiento."))
        events.append(("FINAL_RESULT", "RESULTADO FINAL: RECHAZADO."))
        return _save_validation(
            db,
            request=request,
            maintenance=last_maintenance,
            status=STATUS_REJECTED,
            message="La información del documento no es coherente con el último mantenimiento registrado.",
            reasons=coherence_reasons,
            kilometer_limit=None,
            date_limit=None,
            meets_kilometer_condition=None,
            meets_time_condition=None,
            events=events,
        )

    window: MaintenanceWindow = evaluate_maintenance_window(
        last_maintenance_date=last_maintenance.maintenance_date,
        last_maintenance_odometer=last_maintenance.odometer,
        document_date=request.document_date,
        document_odometer=request.document_odometer,
    )
    events.extend(
        [
            ("KILOMETER_LIMIT", f"Límite por kilometraje calculado: {window.kilometer_limit:,} km."),
            ("DATE_LIMIT", f"Límite por fecha calculado: {window.date_limit:%d/%m/%Y}."),
            (
                "KILOMETER_CONDITION",
                f"Condición por kilometraje: {'CUMPLIDA' if window.meets_kilometer_condition else 'PENDIENTE'}.",
            ),
            ("TIME_CONDITION", f"Condición por tiempo: {'CUMPLIDA' if window.meets_time_condition else 'PENDIENTE'}.") ,
        ]
    )
    status = STATUS_APPROVED if window.is_due else STATUS_OUTSIDE_WINDOW
    message = (
        "El mantenimiento cumple la ventana establecida de 10,000 km o 6 meses."
        if window.is_due
        else "El documento es válido, pero aún no se cumple la ventana de 10,000 km ni 6 meses."
    )
    events.append(("FINAL_RESULT", f"RESULTADO FINAL: {status}."))
    return _save_validation(
        db,
        request=request,
        maintenance=last_maintenance,
        status=status,
        message=message,
        reasons=[],
        kilometer_limit=window.kilometer_limit,
        date_limit=window.date_limit,
        meets_kilometer_condition=window.meets_kilometer_condition,
        meets_time_condition=window.meets_time_condition,
        events=events,
    )


def get_validation_or_raise(db: Session, validation_id: int) -> ValidationRead | None:
    validation = db.scalar(
        select(Validation)
        .options(
            selectinload(Validation.vehicle),
            selectinload(Validation.maintenance),
            selectinload(Validation.audit_logs),
        )
        .where(Validation.id == validation_id)
    )
    return _validation_read(validation) if validation else None


def list_validations(
    db: Session,
    *,
    vehicle_id: Optional[int] = None,
    status: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[ValidationRead]:
    query = select(Validation).options(selectinload(Validation.vehicle), selectinload(Validation.maintenance))
    if vehicle_id:
        query = query.where(Validation.vehicle_id == vehicle_id)
    if status:
        query = query.where(Validation.status == status)
    if date_from:
        query = query.where(Validation.created_at >= date_from)
    if date_to:
        query = query.where(Validation.created_at < date_to.fromordinal(date_to.toordinal() + 1))
    query = query.order_by(Validation.created_at.desc(), Validation.id.desc())
    validations = list(db.scalars(query))
    # El listado no carga auditorías: se obtienen por detalle para mantenerlo ligero.
    return [_validation_read_without_audit(validation) for validation in validations]


def _validation_read_without_audit(validation: Validation) -> ValidationRead:
    response = _validation_read_base(validation)
    response.audit_logs = []
    return response


def _validation_read_base(validation: Validation) -> ValidationRead:
    maintenance = validation.maintenance
    details = validation.analysis_details or {}
    previous = details.get("previous_maintenance") or {}
    return ValidationRead(
        id=validation.id,
        validation_code=validation.validation_code,
        vehicle_id=validation.vehicle_id,
        vehicle_internal_number=validation.vehicle.internal_number if validation.vehicle else None,
        document_id=validation.document_id,
        maintenance_id=validation.maintenance_id,
        status=validation.status,
        document_date=validation.document_date,
        document_odometer=validation.document_odometer,
        last_maintenance_date=maintenance.maintenance_date if maintenance else previous.get("service_date"),
        last_maintenance_odometer=maintenance.odometer if maintenance else previous.get("mileage_km"),
        kilometer_limit=validation.kilometer_limit,
        date_limit=validation.date_limit,
        meets_kilometer_condition=validation.meets_kilometer_condition,
        meets_time_condition=validation.meets_time_condition,
        message=validation.message,
        reasons=validation.reasons or [],
        analysis_details=validation.analysis_details,
        created_at=validation.created_at,
        audit_logs=[],
    )


def get_audit_logs(db: Session, validation_id: int) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog).where(AuditLog.validation_id == validation_id).order_by(AuditLog.created_at, AuditLog.id)
        )
    )
