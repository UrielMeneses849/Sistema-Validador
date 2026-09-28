from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_brand(value: str) -> str:
    normalized = " ".join(value.split()).upper()
    if not normalized:
        raise ValueError("La marca no puede estar vacía.")
    return normalized


class ManufacturerMaintenanceRuleCreate(BaseModel):
    brand: str = Field(min_length=1, max_length=80)
    interval_months: int = Field(gt=0)
    interval_km: int = Field(gt=0)
    active: bool = True

    @field_validator("brand", mode="before")
    @classmethod
    def normalize_brand_name(cls, value: str) -> str:
        return normalize_brand(value)


class ManufacturerMaintenanceRuleUpdate(BaseModel):
    brand: Optional[str] = Field(default=None, min_length=1, max_length=80)
    interval_months: Optional[int] = Field(default=None, gt=0)
    interval_km: Optional[int] = Field(default=None, gt=0)
    active: Optional[bool] = None

    @field_validator("brand", mode="before")
    @classmethod
    def normalize_optional_brand_name(cls, value: Optional[str]) -> Optional[str]:
        return normalize_brand(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> "ManufacturerMaintenanceRuleUpdate":
        null_fields = [
            name
            for name in self.model_fields_set
            if getattr(self, name) is None
        ]
        if null_fields:
            raise ValueError(
                f"Estos campos no aceptan valores nulos: {', '.join(sorted(null_fields))}."
            )
        return self


class ManufacturerMaintenanceRuleRead(ManufacturerMaintenanceRuleCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
