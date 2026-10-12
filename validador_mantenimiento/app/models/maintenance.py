from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


class Maintenance(Base):
    __tablename__ = "maintenances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False, index=True)
    maintenance_date: Mapped[date] = mapped_column(Date, nullable=False)
    odometer: Mapped[int] = mapped_column(Integer, nullable=False)
    maintenance_type: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resets_maintenance_interval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    vehicle: Mapped["Vehicle"] = relationship(back_populates="maintenances")
    validations: Mapped[list["Validation"]] = relationship(back_populates="maintenance")
