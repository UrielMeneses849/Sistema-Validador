from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


class ServiceEvent(Base):
    """Evento normalizado encontrado en un documento; un documento puede producir varios."""

    __tablename__ = "service_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False, index=True)
    source_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(40), nullable=False)
    service_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    service_date_raw: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    mileage_km: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    mileage_raw: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    repair_order_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    dealer: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    service_category: Mapped[str] = mapped_column(String(60), nullable=False, default="unknown")
    service_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resets_maintenance_interval: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, index=True)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False, default="low")
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    field_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    document: Mapped["Document"] = relationship(back_populates="service_events")
    vehicle: Mapped["Vehicle"] = relationship(back_populates="service_events")
