from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: int
    original_filename: str
    stored_filename: str
    mime_type: str
    file_size: int
    uploaded_at: datetime

