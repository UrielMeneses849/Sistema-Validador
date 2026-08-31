from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


class Validation(Base):
    __tablename__ = "validations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    validation_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    maintenance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("maintenances.id"), nullable=True)
    document_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    document_odometer: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    maintenance_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    service_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    kilometer_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    date_limit: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    meets_kilometer_condition: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    meets_time_condition: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    analysis_details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    vehicle: Mapped["Vehicle"] = relationship(back_populates="validations")
    document: Mapped["Document"] = relationship(back_populates="validations")
    maintenance: Mapped[Optional["Maintenance"]] = relationship(back_populates="validations")
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="validation", cascade="all, delete-orphan", order_by="AuditLog.created_at"
    )
