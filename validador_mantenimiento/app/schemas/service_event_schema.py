from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ServiceEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    vehicle_id: int
    source_page: Optional[int]
    extraction_method: str
    service_date: Optional[date]
    service_date_raw: Optional[str]
    mileage_km: Optional[int]
    mileage_raw: Optional[str]
    repair_order_number: Optional[str]
    dealer: Optional[str]
    service_category: str
    service_type: Optional[str]
    description: Optional[str]
    resets_maintenance_interval: Optional[bool]
    confidence: str
    requires_human_review: bool
    user_confirmed: bool
    field_evidence: dict[str, Any]
    warnings: list[str]
    created_at: datetime
    updated_at: datetime


class ServiceEventUpdate(BaseModel):
    """Corrección humana opcional de un evento extraído automáticamente."""

    service_date: Optional[date] = None
    mileage_km: Optional[int] = Field(default=None, ge=0)
    repair_order_number: Optional[str] = Field(default=None, max_length=120)
    dealer: Optional[str] = Field(default=None, max_length=200)
    service_category: Optional[str] = Field(default=None, max_length=60)
    service_type: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=3000)
    resets_maintenance_interval: Optional[bool] = None


class ManualServiceEventCreate(BaseModel):
    document_id: int = Field(gt=0)
    service_date: Optional[date] = None
    mileage_km: Optional[int] = Field(default=None, ge=0)
    repair_order_number: Optional[str] = Field(default=None, max_length=120)
    dealer: Optional[str] = Field(default=None, max_length=200)
    service_type: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=3000)
    resets_maintenance_interval: Optional[bool] = None
