from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VehicleBase(BaseModel):
    internal_number: str = Field(min_length=1, max_length=60)
    plate: str = Field(min_length=1, max_length=30)
    vin: Optional[str] = Field(default=None, max_length=80)
    brand: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=80)
    year: int = Field(ge=1886, le=2100)
    current_odometer: int = Field(ge=0)
    status: Literal["active", "inactive"] = "active"

    @field_validator("internal_number", "plate", "brand", "model", mode="before")
    @classmethod
    def strip_required_values(cls, value: str) -> str:
        value = value.strip() if isinstance(value, str) else value
        if not value:
            raise ValueError("Este campo no puede estar vacío.")
        return value

    @field_validator("vin", mode="before")
    @classmethod
    def normalize_vin(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() or None if isinstance(value, str) else value


class VehicleCreate(VehicleBase):
    pass


class VehicleUpdate(BaseModel):
    internal_number: Optional[str] = Field(default=None, min_length=1, max_length=60)
    plate: Optional[str] = Field(default=None, min_length=1, max_length=30)
    vin: Optional[str] = Field(default=None, max_length=80)
    brand: Optional[str] = Field(default=None, min_length=1, max_length=80)
    model: Optional[str] = Field(default=None, min_length=1, max_length=80)
    year: Optional[int] = Field(default=None, ge=1886, le=2100)
    current_odometer: Optional[int] = Field(default=None, ge=0)
    status: Optional[Literal["active", "inactive"]] = None


class VehicleRead(VehicleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime

