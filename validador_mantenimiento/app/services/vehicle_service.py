from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import Select, exists, or_, select
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


class VehicleHasHistoryError(Exception):
    pass


VEHICLE_HISTORY_CONFLICT_MESSAGE = (
    "No se puede eliminar este vehículo porque tiene mantenimientos, documentos "
    "o validaciones asociados. Su historial debe conservarse para trazabilidad."
)


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

    candidate = INITIAL_CONTRACT_NUMBER if highest is None else highest + 1
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

    # El valor enviado por la vista es sólo una previsualización. La asignación
    # final ocurre aquí para que nunca pueda editarse ni duplicarse desde el
    # cliente. El índice único de `numero_contrato` es la garantía definitiva
    # ante dos altas simultáneas; ante una colisión se vuelve a calcular.
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


def _vehicle_has_related_history(db: Session, vehicle_id: int) -> bool:
    """Revisa relaciones directas y transitivas sin cargar ni borrar el historial."""
    checks = (
        select(exists().where(Maintenance.vehicle_id == vehicle_id)),
        select(exists().where(Document.vehicle_id == vehicle_id)),
        select(exists().where(ServiceEvent.vehicle_id == vehicle_id)),
        select(exists().where(Validation.vehicle_id == vehicle_id)),
        select(
            exists().where(
                DocumentAnalysis.document_id == Document.id,
                Document.vehicle_id == vehicle_id,
            )
        ),
        select(
            exists().where(
                AuditLog.validation_id == Validation.id,
                Validation.vehicle_id == vehicle_id,
            )
        ),
    )
    return any(bool(db.scalar(check)) for check in checks)


def delete_vehicle(db: Session, vehicle_id: int) -> None:
    vehicle = get_vehicle_or_raise(db, vehicle_id)
    if _vehicle_has_related_history(db, vehicle_id):
        raise VehicleHasHistoryError(VEHICLE_HISTORY_CONFLICT_MESSAGE)

    db.delete(vehicle)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Protección adicional si en el futuro se agrega una relación que aún
        # no forme parte de las comprobaciones explícitas anteriores.
        raise VehicleHasHistoryError(VEHICLE_HISTORY_CONFLICT_MESSAGE) from exc
