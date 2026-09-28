from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from sqlalchemy import Select, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis
from app.models.maintenance import Maintenance
from app.models.service_event import ServiceEvent
from app.models.validation import Validation
from app.models.vehicle import Vehicle
from app.schemas.vehicle_schema import VehicleCreate, VehicleUpdate
from app.services.training_dataset_service import resolve_evidence_crop


class VehicleNotFoundError(Exception):
    pass


class DuplicateInternalNumberError(Exception):
    pass


class ContractNumberUnavailableError(Exception):
    pass


class ContractNumberImmutableError(Exception):
    pass


class ContractDateRangeError(Exception):
    pass


class InitialOdometerImmutableError(Exception):
    pass


class VehicleBusinessDataError(Exception):
    pass


class VehicleDeletionError(Exception):
    pass


INITIAL_CONTRACT_NUMBER = 835414
MAX_CONTRACT_NUMBER = 999999
CONTRACT_NUMBER_PATTERN = re.compile(r"^\d{6}$")
CONTRACT_CREATION_ATTEMPTS = 3


def get_vehicle_or_raise(db: Session, vehicle_id: int) -> Vehicle:
    vehicle = db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise VehicleNotFoundError(f"No existe el vehículo con ID {vehicle_id}.")
    return vehicle


def list_vehicles(db: Session, search: Optional[str] = None, status: Optional[str] = None) -> list[Vehicle]:
    query: Select[tuple[Vehicle]] = select(Vehicle).order_by(
        Vehicle.numero_contrato.is_(None), Vehicle.numero_contrato.desc(), Vehicle.internal_number
    )
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                Vehicle.numero_contrato.ilike(term),
                Vehicle.internal_number.ilike(term),
                Vehicle.plate.ilike(term),
                Vehicle.brand.ilike(term),
                Vehicle.model.ilike(term),
            )
        )
    if status:
        query = query.where(Vehicle.status == status)
    return list(db.scalars(query))


def _contract_number(value: object) -> int | None:
    if isinstance(value, str) and CONTRACT_NUMBER_PATTERN.fullmatch(value.strip()):
        return int(value)
    return None


def get_next_contract_number(db: Session) -> str:
    """Obtiene el siguiente consecutivo sin reinterpretar contratos no válidos.

    `internal_number` se incluye únicamente como resguardo para instalaciones
    heredadas que ya usaban un identificador numérico de seis dígitos. Así no
    se intenta reutilizar una clave única existente al crear el nuevo contrato.
    """
    highest = None
    rows = db.execute(select(Vehicle.numero_contrato, Vehicle.internal_number))
    for numero_contrato, internal_number in rows:
        for value in (numero_contrato, internal_number):
            number = _contract_number(value)
            if number is not None:
                highest = number if highest is None else max(highest, number)

    candidate = INITIAL_CONTRACT_NUMBER if highest is None else max(INITIAL_CONTRACT_NUMBER, highest + 1)
    if candidate > MAX_CONTRACT_NUMBER:
        raise ContractNumberUnavailableError("No hay números de contrato de seis dígitos disponibles.")
    return str(candidate).zfill(6)


def _is_contract_registration(payload: VehicleCreate) -> bool:
    return any(
        value is not None
        for value in (
            payload.numero_contrato,
            payload.kilometraje,
            payload.fecha_factura_origen,
            payload.fecha_inicio_contrato,
            payload.fecha_fin_contrato,
        )
    )


def _contract_vehicle_values(payload: VehicleCreate, contract_number: str) -> dict:
    """Mapea la captura actual a las columnas heredadas sin exponerlas al formulario."""
    assert payload.kilometraje is not None
    assert payload.fecha_factura_origen is not None

    values = payload.model_dump()
    values.update(
        {
            # El contrato es el identificador operativo que también conserva
            # legibilidad en las pantallas heredadas que usaban internal_number.
            "numero_contrato": contract_number,
            "internal_number": contract_number,
            # Estas columnas siguen existiendo en SQLite por compatibilidad con
            # registros previos. Sus valores se derivan en servidor y no forman
            # parte de la captura manual actual.
            "plate": f"CONTRATO-{contract_number}",
            "year": payload.fecha_factura_origen.year,
            "vin": None,
            "current_odometer": payload.kilometraje,
            "kilometraje": payload.kilometraje,
            "status": "active",
        }
    )
    return values


def _legacy_vehicle_values(payload: VehicleCreate) -> dict:
    """Mantiene operativas las altas API anteriores durante la transición."""
    values = payload.model_dump()
    if values.get("kilometraje") is None:
        values["kilometraje"] = values.get("current_odometer")
    return values


def create_vehicle(db: Session, payload: VehicleCreate) -> Vehicle:
    if not _is_contract_registration(payload):
        vehicle = Vehicle(**_legacy_vehicle_values(payload))
        db.add(vehicle)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise DuplicateInternalNumberError("El número económico ya está registrado.") from exc
        db.refresh(vehicle)
        return vehicle

    # Un número leído del PDF pertenece al contrato y se conserva. Si el alta
    # no lo incluye, el servidor continúa asignando el siguiente consecutivo.
    if payload.numero_contrato is not None:
        contract_number = payload.numero_contrato
        vehicle = Vehicle(**_contract_vehicle_values(payload, contract_number))
        db.add(vehicle)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise DuplicateInternalNumberError(
                f"El contrato {contract_number} ya está registrado."
            ) from exc
        db.refresh(vehicle)
        return vehicle

    # Para altas manuales, el índice único es la garantía definitiva ante dos
    # solicitudes simultáneas; ante una colisión se vuelve a calcular.
    last_error: IntegrityError | None = None
    for _ in range(CONTRACT_CREATION_ATTEMPTS):
        contract_number = get_next_contract_number(db)
        vehicle = Vehicle(**_contract_vehicle_values(payload, contract_number))
        db.add(vehicle)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            last_error = exc
            continue
        db.refresh(vehicle)
        return vehicle

    raise DuplicateInternalNumberError(
        "No fue posible asignar un número de contrato único. Intenta nuevamente."
    ) from last_error


def update_vehicle(db: Session, vehicle_id: int, payload: VehicleUpdate) -> Vehicle:
    vehicle = get_vehicle_or_raise(db, vehicle_id)
    values = payload.model_dump(exclude_unset=True)

    if "numero_contrato" in values:
        if values["numero_contrato"] != vehicle.numero_contrato:
            raise ContractNumberImmutableError("El número de contrato no se puede modificar.")
        values.pop("numero_contrato")

    if "kilometraje" in values:
        values["current_odometer"] = values["kilometraje"]
    elif "current_odometer" in values:
        # Mantiene sincronizada la API heredada con el campo usado por el
        # formulario vigente y por los listados.
        values["kilometraje"] = values["current_odometer"]

    if "initial_odometer" in values and vehicle.initial_odometer is not None:
        if values["initial_odometer"] != vehicle.initial_odometer:
            raise InitialOdometerImmutableError(
                "El kilometraje al inicio del contrato no se puede modificar."
            )
        values.pop("initial_odometer")

    target_condition = values.get("vehicle_condition", vehicle.vehicle_condition)
    target_initial_odometer = values.get("initial_odometer", vehicle.initial_odometer)
    if target_condition in {"new", "used"} and target_initial_odometer is None:
        raise VehicleBusinessDataError(
            "El kilometraje al inicio del contrato es obligatorio para Nuevo (M1) y Seminuevo (M2)."
        )

    start_date = values.get("fecha_inicio_contrato", vehicle.fecha_inicio_contrato)
    end_date = values.get("fecha_fin_contrato", vehicle.fecha_fin_contrato)
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ContractDateRangeError("La fecha de fin de contrato no puede ser anterior a la fecha de inicio.")

    for field, value in values.items():
        setattr(vehicle, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateInternalNumberError("El número económico ya está registrado.") from exc
    db.refresh(vehicle)
    return vehicle


def delete_vehicle(db: Session, vehicle_id: int) -> None:
    """Elimina el vehículo y todo su historial dependiente de forma explícita."""
    vehicle = get_vehicle_or_raise(db, vehicle_id)
    documents = list(
        db.scalars(select(Document).where(Document.vehicle_id == vehicle_id))
    )
    document_ids = [document.id for document in documents]
    events = list(
        db.scalars(select(ServiceEvent).where(ServiceEvent.vehicle_id == vehicle_id))
    )
    validations = list(
        db.scalars(select(Validation).where(Validation.vehicle_id == vehicle_id))
    )
    validation_ids = [validation.id for validation in validations]
    document_paths = [Path(document.file_path) for document in documents]
    evidence_paths: set[Path] = set()
    for event in events:
        evidence = event.field_evidence or {}
        for field_name in ("date", "mileage_km"):
            field = evidence.get(field_name)
            crop_id = field.get("crop_id") if isinstance(field, dict) else None
            if not crop_id:
                continue
            try:
                evidence_paths.add(resolve_evidence_crop(str(crop_id)))
            except ValueError:
                continue

    try:
        if validation_ids:
            for audit_log in db.scalars(
                select(AuditLog).where(AuditLog.validation_id.in_(validation_ids))
            ):
                db.delete(audit_log)
        for validation in validations:
            db.delete(validation)
        for event in events:
            db.delete(event)
        if document_ids:
            for analysis in db.scalars(
                select(DocumentAnalysis).where(
                    DocumentAnalysis.document_id.in_(document_ids)
                )
            ):
                db.delete(analysis)
        for document in documents:
            db.delete(document)
        for maintenance in db.scalars(
            select(Maintenance).where(Maintenance.vehicle_id == vehicle_id)
        ):
            db.delete(maintenance)
        db.delete(vehicle)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise VehicleDeletionError(
            "No fue posible eliminar completamente el vehículo y sus datos asociados."
        ) from exc

    for path in [*document_paths, *evidence_paths]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # La eliminación de la base ya fue confirmada. Un archivo huérfano
            # no debe restaurar datos ni hacer parecer fallida la operación.
            pass
