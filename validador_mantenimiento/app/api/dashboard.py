from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends

from app.database.database import get_db
from app.models.validation import Validation
from app.models.vehicle import Vehicle
from app.services.validation_service import list_validations


router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@router.get("")
def get_dashboard(db: Session = Depends(get_db)) -> dict:
    status_counts = {status: count for status, count in db.execute(select(Validation.status, func.count()).group_by(Validation.status))}
    return {
        "vehicles": db.scalar(select(func.count()).select_from(Vehicle)) or 0,
        "validations": db.scalar(select(func.count()).select_from(Validation)) or 0,
        "approved": status_counts.get("APROBADO", 0)
        + status_counts.get("COMPLIANT", 0)
        + status_counts.get("TOLERANCE_PERIOD", 0),
        "rejected": status_counts.get("RECHAZADO", 0),
        "outside_window": status_counts.get("FUERA_DE_VENTANA", 0)
        + status_counts.get("EXCEEDED_MILEAGE", 0)
        + status_counts.get("EXCEEDED_TIME", 0)
        + status_counts.get("EXCEEDED_BOTH", 0),
        "review_required": status_counts.get("REVISION_REQUERIDA", 0)
        + status_counts.get("REQUIRES_REVIEW", 0)
        + status_counts.get("INSUFFICIENT_DATA", 0),
        "first_maintenance_available": status_counts.get("FIRST_MAINTENANCE_AVAILABLE", 0),
        "latest_validations": [item.model_dump(mode="json") for item in list_validations(db)[:8]],
    }
