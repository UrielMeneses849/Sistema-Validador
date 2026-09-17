from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.manufacturer_maintenance_rule import ManufacturerMaintenanceRule
from app.schemas.manufacturer_maintenance_rule_schema import (
    ManufacturerMaintenanceRuleCreate,
    ManufacturerMaintenanceRuleUpdate,
    normalize_brand,
)


class ManufacturerMaintenanceRuleNotFoundError(Exception):
    pass


class DuplicateManufacturerMaintenanceRuleError(Exception):
    pass


def get_rule_or_raise(db: Session, rule_id: int) -> ManufacturerMaintenanceRule:
    rule = db.get(ManufacturerMaintenanceRule, rule_id)
    if not rule:
        raise ManufacturerMaintenanceRuleNotFoundError(
            f"No existe la política de fabricante con ID {rule_id}."
        )
    return rule


def get_active_rule_by_brand(db: Session, brand: str) -> ManufacturerMaintenanceRule | None:
    normalized_brand = normalize_brand(brand)
    return db.scalar(
        select(ManufacturerMaintenanceRule).where(
            ManufacturerMaintenanceRule.brand == normalized_brand,
            ManufacturerMaintenanceRule.active.is_(True),
        )
    )


def list_rules(db: Session, active: Optional[bool] = None) -> list[ManufacturerMaintenanceRule]:
    query = select(ManufacturerMaintenanceRule)
    if active is not None:
        query = query.where(ManufacturerMaintenanceRule.active.is_(active))
    return list(db.scalars(query.order_by(ManufacturerMaintenanceRule.brand)))


def create_rule(
    db: Session, payload: ManufacturerMaintenanceRuleCreate
) -> ManufacturerMaintenanceRule:
    rule = ManufacturerMaintenanceRule(**payload.model_dump())
    db.add(rule)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateManufacturerMaintenanceRuleError(
            f"Ya existe una política para la marca {payload.brand}."
        ) from exc
    db.refresh(rule)
    return rule


def update_rule(
    db: Session, rule_id: int, payload: ManufacturerMaintenanceRuleUpdate
) -> ManufacturerMaintenanceRule:
    rule = get_rule_or_raise(db, rule_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateManufacturerMaintenanceRuleError(
            f"Ya existe una política para la marca {payload.brand}."
        ) from exc
    db.refresh(rule)
    return rule


def deactivate_rule(db: Session, rule_id: int) -> ManufacturerMaintenanceRule:
    rule = get_rule_or_raise(db, rule_id)
    rule.active = False
    db.commit()
    db.refresh(rule)
    return rule
