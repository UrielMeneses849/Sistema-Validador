from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.maintenance import Maintenance
from app.schemas.maintenance_schema import MaintenanceCreate
from app.services.vehicle_service import get_vehicle_or_raise


def register_maintenance(db: Session, vehicle_id: int, payload: MaintenanceCreate) -> Maintenance:
    vehicle = get_vehicle_or_raise(db, vehicle_id)
    maintenance = Maintenance(vehicle_id=vehicle.id, **payload.model_dump())
    vehicle.current_odometer = max(vehicle.current_odometer, payload.odometer)
    # Los vehículos capturados por contrato muestran este mismo valor en la
    # pantalla de vehículos. Mantener ambos campos sincronizados preserva la
    # compatibilidad con el historial de mantenimientos existente.
    if vehicle.kilometraje is not None:
        vehicle.kilometraje = vehicle.current_odometer
    db.add(maintenance)
    db.commit()
    db.refresh(maintenance)
    return maintenance


def list_maintenances(db: Session, vehicle_id: int) -> list[Maintenance]:
    get_vehicle_or_raise(db, vehicle_id)
    return list(
        db.scalars(
            select(Maintenance)
            .where(Maintenance.vehicle_id == vehicle_id)
            .order_by(Maintenance.maintenance_date.desc(), Maintenance.id.desc())
        )
    )


def get_latest_maintenance(db: Session, vehicle_id: int) -> Maintenance | None:
    return db.scalar(
        select(Maintenance)
        .where(Maintenance.vehicle_id == vehicle_id)
        .order_by(Maintenance.maintenance_date.desc(), Maintenance.id.desc())
        .limit(1)
    )
