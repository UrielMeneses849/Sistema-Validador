from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MaintenanceCreate(BaseModel):
    maintenance_date: date
    odometer: int = Field(ge=0)
    maintenance_type: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=3000)

    @field_validator("maintenance_date")
    @classmethod
    def date_cannot_be_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("La fecha de mantenimiento no puede ser futura.")
        return value


class MaintenanceRead(MaintenanceCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: int
    created_at: datetime

