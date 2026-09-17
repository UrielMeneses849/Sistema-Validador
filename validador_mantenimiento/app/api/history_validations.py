from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.history_validation_schema import HistoryValidationRead, HistoryValidationRequest
from app.services.history_validation_service import (
    HistoryVehicleMismatchError,
    validate_maintenance_history,
)
from app.services.service_event_service import ServiceEventNotFoundError
from app.services.vehicle_service import VehicleNotFoundError


router = APIRouter(prefix="/api/history-validations", tags=["Validación de historial"])


@router.post("", response_model=HistoryValidationRead, status_code=status.HTTP_201_CREATED)
def validate_history(
    payload: HistoryValidationRequest, db: Session = Depends(get_db)
) -> HistoryValidationRead:
    try:
        return validate_maintenance_history(db, payload)
    except (VehicleNotFoundError, ServiceEventNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except HistoryVehicleMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
