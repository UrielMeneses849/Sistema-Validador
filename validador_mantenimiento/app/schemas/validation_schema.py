from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ValidationRequest(BaseModel):
    vehicle_id: int = Field(gt=0)
    document_id: int = Field(gt=0)
    document_date: Optional[date] = None
    # Se acepta temporalmente un valor negativo para convertirlo en un resultado
    # RECHAZADO trazable, en lugar de perder el intento con un 422.
    document_odometer: Optional[int] = None
    maintenance_type: Optional[str] = Field(default=None, max_length=120)
    service_description: Optional[str] = Field(default=None, max_length=3000)
    provider_name: Optional[str] = Field(default=None, max_length=160)
    invoice_number: Optional[str] = Field(default=None, max_length=120)


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    message: str
    created_at: datetime

class ValidationRead(BaseModel):
    id: int
    validation_code: str
    vehicle_id: int
    vehicle_internal_number: Optional[str] = None
    document_id: int
    maintenance_id: Optional[int]
    status: str
    document_date: Optional[date]
    document_odometer: Optional[int]
    last_maintenance_date: Optional[date]
    last_maintenance_odometer: Optional[int]
    kilometer_limit: Optional[int]
    date_limit: Optional[date]
    meets_kilometer_condition: Optional[bool]
    meets_time_condition: Optional[bool]
    message: str
    reasons: list[str]
    analysis_details: Optional[dict[str, Any]] = None
    created_at: datetime
    audit_logs: list[AuditLogRead] = Field(default_factory=list)
