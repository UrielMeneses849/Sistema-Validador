from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.service_event_schema import ServiceEventRead


class DocumentAnalysisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    extraction_method: str
    extraction_status: str
    document_type: str
    confidence: str
    requires_human_review: bool
    warnings: list[str]
    extracted_fields: dict[str, Any]
    extracted_text: str | None = None
    created_at: datetime
    updated_at: datetime
    service_events: list[ServiceEventRead] = Field(default_factory=list)
