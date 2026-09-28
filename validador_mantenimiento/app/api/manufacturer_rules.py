from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.manufacturer_maintenance_rule_schema import (
    ManufacturerMaintenanceRuleCreate,
    ManufacturerMaintenanceRuleRead,
    ManufacturerMaintenanceRuleUpdate,
)
from app.services.manufacturer_maintenance_rule_service import (
    DuplicateManufacturerMaintenanceRuleError,
    ManufacturerMaintenanceRuleNotFoundError,
    create_rule,
    deactivate_rule,
    get_rule_or_raise,
    list_rules,
    update_rule,
)


router = APIRouter(prefix="/api/manufacturer-rules", tags=["Políticas de fabricantes"])


def _not_found(error: ManufacturerMaintenanceRuleNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))


@router.post("", response_model=ManufacturerMaintenanceRuleRead, status_code=status.HTTP_201_CREATED)
def create(
    payload: ManufacturerMaintenanceRuleCreate, db: Session = Depends(get_db)
) -> ManufacturerMaintenanceRuleRead:
    try:
        return create_rule(db, payload)
    except DuplicateManufacturerMaintenanceRuleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("", response_model=list[ManufacturerMaintenanceRuleRead])
def get_all(
    active: Optional[bool] = None, db: Session = Depends(get_db)
) -> list[ManufacturerMaintenanceRuleRead]:
    return list_rules(db, active=active)


@router.get("/{rule_id}", response_model=ManufacturerMaintenanceRuleRead)
def get_one(rule_id: int, db: Session = Depends(get_db)) -> ManufacturerMaintenanceRuleRead:
    try:
        return get_rule_or_raise(db, rule_id)
    except ManufacturerMaintenanceRuleNotFoundError as exc:
        raise _not_found(exc) from exc


@router.put("/{rule_id}", response_model=ManufacturerMaintenanceRuleRead)
def update(
    rule_id: int,
    payload: ManufacturerMaintenanceRuleUpdate,
    db: Session = Depends(get_db),
) -> ManufacturerMaintenanceRuleRead:
    try:
        return update_rule(db, rule_id, payload)
    except ManufacturerMaintenanceRuleNotFoundError as exc:
        raise _not_found(exc) from exc
    except DuplicateManufacturerMaintenanceRuleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/{rule_id}", response_model=ManufacturerMaintenanceRuleRead)
def deactivate(rule_id: int, db: Session = Depends(get_db)) -> ManufacturerMaintenanceRuleRead:
    try:
        return deactivate_rule(db, rule_id)
    except ManufacturerMaintenanceRuleNotFoundError as exc:
        raise _not_found(exc) from exc
