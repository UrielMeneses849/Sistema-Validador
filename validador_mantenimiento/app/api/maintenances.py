from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.maintenance_schema import MaintenanceCreate, MaintenanceRead
from app.services.maintenance_service import list_maintenances, register_maintenance
from app.services.vehicle_service import VehicleNotFoundError


router = APIRouter(prefix="/api/vehicles/{vehicle_id}/maintenances", tags=["Mantenimientos"])


@router.post("", response_model=MaintenanceRead, status_code=status.HTTP_201_CREATED)
def create(vehicle_id: int, payload: MaintenanceCreate, db: Session = Depends(get_db)) -> MaintenanceRead:
    try:
        return register_maintenance(db, vehicle_id, payload)
    except VehicleNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("", response_model=list[MaintenanceRead])
def get_all(vehicle_id: int, db: Session = Depends(get_db)) -> list[MaintenanceRead]:
    try:
        return list_maintenances(db, vehicle_id)
    except VehicleNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

