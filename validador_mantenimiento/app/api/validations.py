from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.validation_schema import AuditLogRead, ValidationRead, ValidationRequest
from app.services.document_service import DocumentNotFoundError
from app.services.vehicle_service import VehicleNotFoundError
from app.services.validation_service import get_audit_logs, get_validation_or_raise, list_validations, validate_document


router = APIRouter(prefix="/api/validations", tags=["Validaciones"])


@router.post("", response_model=ValidationRead, status_code=status.HTTP_201_CREATED)
def create(payload: ValidationRequest, db: Session = Depends(get_db)) -> ValidationRead:
    try:
        return validate_document(db, payload)
    except (VehicleNotFoundError, DocumentNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("", response_model=list[ValidationRead])
def get_all(
    vehicle_id: Optional[int] = None,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
) -> list[ValidationRead]:
    return list_validations(
        db, vehicle_id=vehicle_id, status=status_filter, date_from=date_from, date_to=date_to
    )


@router.get("/{validation_id}", response_model=ValidationRead)
def get_one(validation_id: int, db: Session = Depends(get_db)) -> ValidationRead:
    validation = get_validation_or_raise(db, validation_id)
    if not validation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No existe la validación solicitada.")
    return validation


@router.get("/{validation_id}/audit", response_model=list[AuditLogRead])
def get_audit(validation_id: int, db: Session = Depends(get_db)) -> list[AuditLogRead]:
    validation = get_validation_or_raise(db, validation_id)
    if not validation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No existe la validación solicitada.")
    return get_audit_logs(db, validation_id)

