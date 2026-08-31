from __future__ import annotations

from typing import Optional

from sqlalchemy import Select, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.vehicle import Vehicle
from app.schemas.vehicle_schema import VehicleCreate, VehicleUpdate


class VehicleNotFoundError(Exception):
    pass


class DuplicateInternalNumberError(Exception):
    pass


def get_vehicle_or_raise(db: Session, vehicle_id: int) -> Vehicle:
    vehicle = db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise VehicleNotFoundError(f"No existe el vehículo con ID {vehicle_id}.")
    return vehicle


def list_vehicles(db: Session, search: Optional[str] = None, status: Optional[str] = None) -> list[Vehicle]:
    query: Select[tuple[Vehicle]] = select(Vehicle).order_by(Vehicle.internal_number)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            or_(Vehicle.internal_number.ilike(term), Vehicle.plate.ilike(term), Vehicle.brand.ilike(term))
        )
    if status:
        query = query.where(Vehicle.status == status)
    return list(db.scalars(query))


def create_vehicle(db: Session, payload: VehicleCreate) -> Vehicle:
    vehicle = Vehicle(**payload.model_dump())
    db.add(vehicle)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateInternalNumberError("El número económico ya está registrado.") from exc
    db.refresh(vehicle)
    return vehicle


def update_vehicle(db: Session, vehicle_id: int, payload: VehicleUpdate) -> Vehicle:
    vehicle = get_vehicle_or_raise(db, vehicle_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(vehicle, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateInternalNumberError("El número económico ya está registrado.") from exc
    db.refresh(vehicle)
    return vehicle


def deactivate_vehicle(db: Session, vehicle_id: int) -> Vehicle:
    vehicle = get_vehicle_or_raise(db, vehicle_id)
    vehicle.status = "inactive"
    db.commit()
    db.refresh(vehicle)
    return vehicle

