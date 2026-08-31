from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


class DocumentAnalysis(Base):
    """Resultado reproducible de analizar un archivo sin modificar su original."""

    __tablename__ = "document_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), unique=True, nullable=False, index=True)
    extraction_method: Mapped[str] = mapped_column(String(40), nullable=False)
    extraction_status: Mapped[str] = mapped_column(String(40), nullable=False, default="completed")
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    document_type: Mapped[str] = mapped_column(String(60), nullable=False, default="desconocido")
    confidence: Mapped[str] = mapped_column(String(20), nullable=False, default="low")
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    extracted_fields: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    document: Mapped["Document"] = relationship(back_populates="analysis")
