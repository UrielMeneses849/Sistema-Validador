from __future__ import annotations

from datetime import date, datetime
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
from app.services.service_event_service import get_service_event_or_raise
from app.services.timeline_service import (
    AppliedMaintenancePolicy,
    MaintenanceReference,
    TimelineAssessment,
    assess_service_event,
)
from app.services.maintenance_rules import TOLERANCE_KILOMETERS, TOLERANCE_MONTHS
from app.services.vehicle_service import VehicleNotFoundError, get_vehicle_or_raise


STATUS_APPROVED = "APROBADO"
STATUS_OUTSIDE_WINDOW = "FUERA_DE_VENTANA"
STATUS_REJECTED = "RECHAZADO"
STATUS_REVIEW_REQUIRED = "REVISION_REQUERIDA"


def _semantic_validation_state(status: str, details: dict | None = None) -> str:
    if details and details.get("validation_state"):
        return str(details["validation_state"])
    if status == "FIRST_MAINTENANCE_AVAILABLE":
        return "no_previous_maintenance"
    if status == "COMPLIANT":
        return "compliant"
    if status == "TOLERANCE_PERIOD":
        return "tolerance_period"
    if status in {"EXCEEDED_MILEAGE", "EXCEEDED_TIME", "EXCEEDED_BOTH"}:
        return "non_compliant"
    if status == "INSUFFICIENT_DATA":
        return "missing_current_data"
    return "requires_review"


def _validation_read(validation: Validation) -> ValidationRead:
    maintenance = validation.maintenance
    details = validation.analysis_details or {}
    previous = details.get("baseline") or details.get("previous_maintenance") or {}
    return ValidationRead(
        id=validation.id,
        validation_code=validation.validation_code,
        vehicle_id=validation.vehicle_id,
        vehicle_internal_number=(
            validation.vehicle.numero_contrato or validation.vehicle.internal_number
            if validation.vehicle
            else None
        ),
        document_id=validation.document_id,
        maintenance_id=validation.maintenance_id,
        status=validation.status,
        validation_state=_semantic_validation_state(validation.status, details),
        document_date=validation.document_date,
        document_odometer=validation.document_odometer,
        last_maintenance_date=(
            maintenance.maintenance_date
            if maintenance
            else previous.get("date") or previous.get("service_date")
        ),
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
    analysis_details: dict | None = None,
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
        analysis_details=analysis_details,
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


def _reference_payload(reference: MaintenanceReference | None) -> dict | None:
    if reference is None:
        return None
    return {
        "source": reference.source,
        "source_id": reference.source_id,
        "date": reference.service_date.isoformat(),
        "service_date": reference.service_date.isoformat(),
        "mileage_km": reference.mileage_km,
    }


def _policy_payload(policy: AppliedMaintenancePolicy | None) -> dict | None:
    if policy is None:
        return None
    return {
        "source": policy.source,
        "brand": policy.brand,
        "rule_id": policy.rule_id,
        "months": policy.interval_months,
        "kilometers": policy.interval_km,
    }


def _assessment_details(assessment: TimelineAssessment) -> dict:
    baseline = _reference_payload(assessment.previous)
    policy = assessment.policy
    return {
        "vehicle_condition": assessment.vehicle_condition,
        "service_sequence": assessment.service_sequence,
        "baseline": baseline,
        # Se conserva la clave anterior para lectores históricos del API.
        "previous_maintenance": baseline,
        "policy": _policy_payload(assessment.policy),
        "normal_limit": (
            {
                "months": policy.interval_months,
                "kilometers": policy.interval_km,
            }
            if policy
            else None
        ),
        "tolerance": (
            {
                "months": TOLERANCE_MONTHS,
                "kilometers": TOLERANCE_KILOMETERS,
            }
            if policy
            else None
        ),
        "maximum_limit": (
            {
                "months": policy.interval_months + TOLERANCE_MONTHS,
                "kilometers": policy.interval_km + TOLERANCE_KILOMETERS,
            }
            if policy
            else None
        ),
        "calculated_limits": {
            "normal": {
                "date": assessment.normal_date_limit.isoformat()
                if assessment.normal_date_limit
                else None,
                "odometer_km": assessment.normal_kilometer_limit,
            },
            "maximum": {
                "date": assessment.date_limit.isoformat()
                if assessment.date_limit
                else None,
                "odometer_km": assessment.kilometer_limit,
            },
        },
        "limit_evaluation": {
            "normal": {
                "meets_time": assessment.meets_normal_time_condition,
                "meets_kilometers": assessment.meets_normal_kilometer_condition,
            },
            "maximum": {
                "meets_time": assessment.meets_time_condition,
                "meets_kilometers": assessment.meets_kilometer_condition,
            },
        },
        "delta_km": assessment.delta_km,
        "interval_validation": (
            "compliant"
            if assessment.status == "COMPLIANT"
            else "tolerance_period"
            if assessment.status == "TOLERANCE_PERIOD"
            else "not_compliant"
            if assessment.status in {"EXCEEDED_MILEAGE", "EXCEEDED_TIME", "EXCEEDED_BOTH"}
            else "requires_review"
        ),
        "validation_state": assessment.validation_state,
    }


def _save_service_event_validation(
    db: Session, event: ServiceEvent, assessment: TimelineAssessment
) -> ValidationRead:
    previous = assessment.previous
    evidence = event.field_evidence or {}

    def trace_field(field_name: str) -> dict:
        field = evidence.get(field_name) if isinstance(evidence.get(field_name), dict) else {}
        candidates = field.get("candidates") if isinstance(field.get("candidates"), list) else []
        selected = next((candidate for candidate in candidates if candidate.get("selected")), None)
        if selected is None and candidates:
            selected = candidates[0]
        return {
            "selected_value": field.get("normalized_value"),
            "raw_text": field.get("raw_value"),
            "label": selected.get("label") if selected else None,
            "confidence": field.get("confidence"),
            "confidence_score": field.get("confidence_score"),
            "page": (selected.get("page") if selected else None) or field.get("source_page"),
            "selection_reasons": selected.get("reasons", []) if selected else [],
            "ambiguous": bool(field.get("ambiguous")),
            "candidate_count": len(candidates),
            "manually_verified": bool(field.get("manually_verified")),
        }

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
            **_assessment_details(assessment),
            "service_event_id": event.id,
            "document_type": event.document.analysis.document_type if event.document.analysis else None,
            "extraction_method": event.extraction_method,
            "extraction_trace": {
                "method": event.extraction_method,
                "confidence": event.confidence,
                "warnings": event.warnings,
                "fields": {
                    "service_date": trace_field("date"),
                    "actual_mileage": trace_field("mileage_km"),
                },
            },
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
    register_audit_event(
        db,
        validation.id,
        "BUSINESS_CONTEXT",
        f"Condición: {assessment.vehicle_condition}; secuencia: {assessment.service_sequence or 'no aplicable'}.",
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
    if assessment.policy:
        policy_name = (
            f"fabricante {assessment.policy.brand}"
            if assessment.policy.source == "manufacturer"
            else "regla para Seminuevo (M2)"
        )
        register_audit_event(
            db,
            validation.id,
            "MAINTENANCE_POLICY",
            f"Política {policy_name}: {assessment.policy.interval_months} meses y "
            f"{assessment.policy.interval_km:,} km.",
        )
    if assessment.kilometer_limit is not None and assessment.date_limit is not None:
        register_audit_event(
            db,
            validation.id,
            "INTERVAL_LIMITS",
            f"Límites máximos con tolerancia: {assessment.kilometer_limit:,} km y "
            f"{assessment.date_limit:%d/%m/%Y}.",
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


def preview_service_event_validation(
    db: Session, event: ServiceEvent, *, event_scope_ids: set[int]
) -> ValidationRead:
    """Calcula VAL-002 sin crear Validation ni AuditLog persistentes."""
    assessment = assess_service_event(db, event, event_scope_ids)
    previous = assessment.previous
    vehicle = event.vehicle
    details = {
        **_assessment_details(assessment),
        "service_event_id": event.id,
        "document_type": event.document.analysis.document_type
        if event.document.analysis
        else None,
        "extraction_method": event.extraction_method,
    }
    return ValidationRead(
        id=0,
        validation_code=f"PREVIEW-{event.id}",
        vehicle_id=event.vehicle_id,
        vehicle_internal_number=(
            vehicle.numero_contrato or vehicle.internal_number if vehicle else None
        ),
        document_id=event.document_id,
        maintenance_id=(
            previous.maintenance.id
            if previous is not None and previous.maintenance is not None
            else None
        ),
        status=assessment.status,
        validation_state=assessment.validation_state,
        document_date=event.service_date,
        document_odometer=event.mileage_km,
        last_maintenance_date=previous.service_date if previous else None,
        last_maintenance_odometer=previous.mileage_km if previous else None,
        kilometer_limit=assessment.kilometer_limit,
        date_limit=assessment.date_limit,
        meets_kilometer_condition=assessment.meets_kilometer_condition,
        meets_time_condition=assessment.meets_time_condition,
        message=assessment.message,
        reasons=assessment.reasons,
        analysis_details=details,
        created_at=datetime.now(),
        audit_logs=[],
    )


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

    synthetic_event = ServiceEvent(
        document_id=document.id,
        vehicle_id=vehicle.id,
        extraction_method="legacy_manual_validation",
        service_date=request.document_date,
        service_date_raw=request.document_date.isoformat(),
        mileage_km=request.document_odometer,
        mileage_raw=str(request.document_odometer),
        service_category="preventive_maintenance",
        service_type=request.maintenance_type,
        description=request.service_description,
        resets_maintenance_interval=True,
        confidence="high",
        requires_human_review=False,
        user_confirmed=True,
        field_evidence={"extraction_method": "legacy_manual_validation"},
        warnings=[],
    )
    assessment = assess_service_event(db, synthetic_event)
    reference = assessment.previous
    maintenance = reference.maintenance if reference and reference.maintenance else None
    if reference:
        events.append(
            (
                "REFERENCE_FOUND",
                f"Referencia {reference.source}: {reference.service_date:%d/%m/%Y}, "
                f"{reference.mileage_km:,} km.",
            )
        )
    else:
        events.append(("REFERENCE_MISSING", "No fue posible establecer una referencia de mantenimiento."))
    if assessment.kilometer_limit is not None and assessment.date_limit is not None:
        events.extend(
            [
                (
                    "KILOMETER_LIMIT",
                    "Límite máximo por kilometraje con tolerancia: "
                    f"{assessment.kilometer_limit:,} km.",
                ),
                (
                    "DATE_LIMIT",
                    "Límite máximo por fecha con tolerancia: "
                    f"{assessment.date_limit:%d/%m/%Y}.",
                ),
            ]
        )
    events.append(("FINAL_RESULT", f"RESULTADO FINAL: {assessment.status}."))
    return _save_validation(
        db,
        request=request,
        maintenance=maintenance,
        status=assessment.status,
        message=assessment.message,
        reasons=assessment.reasons,
        kilometer_limit=assessment.kilometer_limit,
        date_limit=assessment.date_limit,
        meets_kilometer_condition=assessment.meets_kilometer_condition,
        meets_time_condition=assessment.meets_time_condition,
        events=events,
        analysis_details=_assessment_details(assessment),
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
    previous = details.get("baseline") or details.get("previous_maintenance") or {}
    return ValidationRead(
        id=validation.id,
        validation_code=validation.validation_code,
        vehicle_id=validation.vehicle_id,
        vehicle_internal_number=(
            validation.vehicle.numero_contrato or validation.vehicle.internal_number
            if validation.vehicle
            else None
        ),
        document_id=validation.document_id,
        maintenance_id=validation.maintenance_id,
        status=validation.status,
        validation_state=_semantic_validation_state(validation.status, details),
        document_date=validation.document_date,
        document_odometer=validation.document_odometer,
        last_maintenance_date=(
            maintenance.maintenance_date
            if maintenance
            else previous.get("date") or previous.get("service_date")
        ),
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
