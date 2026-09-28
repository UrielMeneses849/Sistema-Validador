from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.service_event_schema import ServiceEventRead
from app.schemas.validation_schema import ValidationRead


class HistoryValidationRequest(BaseModel):
    vehicle_id: int = Field(gt=0)
    event_ids: list[int] = Field(min_length=1)

    @field_validator("event_ids")
    @classmethod
    def unique_positive_event_ids(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("Los identificadores de eventos deben ser mayores que cero.")
        return list(dict.fromkeys(values))


class HistoryBaselineRead(BaseModel):
    source: Literal["contract_start"] = "contract_start"
    date: Optional[date]
    mileage_km: Optional[int]


class HistoryPolicyRead(BaseModel):
    source: Literal["manufacturer", "used_vehicle_default"]
    brand: Optional[str] = None
    months: int
    kilometers: int
    rule_id: Optional[int] = None


class HistoryDocumentDetailRead(BaseModel):
    id: int
    original_filename: str
    document_type: Optional[str] = None
    extraction_method: Optional[str] = None
    confidence: Optional[str] = None
    requires_human_review: bool = False
    warnings: list[str] = Field(default_factory=list)
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    extracted_text: Optional[str] = None


class HistoryValidationRowRead(BaseModel):
    service_event: ServiceEventRead
    validation: ValidationRead
    elapsed_time: Optional[str] = None
    delta_km: Optional[int] = None
    document: HistoryDocumentDetailRead


class HistorySummaryRead(BaseModel):
    services_analyzed: int
    compliant: int
    tolerance_period: int
    non_compliant: int
    requires_review: int
    result: Literal[
        "COMPLIANT", "TOLERANCE_PERIOD", "NON_COMPLIANT", "REQUIRES_REVIEW"
    ]
    message: str


class HistoryValidationRead(BaseModel):
    vehicle_id: int
    vehicle_condition: str
    ephemeral: bool = False
    preview_document_ids: list[int] = Field(default_factory=list)
    baseline: HistoryBaselineRead
    policy: Optional[HistoryPolicyRead] = None
    policy_reasons: list[str] = Field(default_factory=list)
    rows: list[HistoryValidationRowRead]
    summary: HistorySummaryRead
