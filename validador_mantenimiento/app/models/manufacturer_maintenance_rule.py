from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base


class ManufacturerMaintenanceRule(Base):
    __tablename__ = "manufacturer_maintenance_rules"
    __table_args__ = (
        CheckConstraint("interval_months > 0", name="ck_manufacturer_rule_months_positive"),
        CheckConstraint("interval_km > 0", name="ck_manufacturer_rule_km_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    interval_months: Mapped[int] = mapped_column(Integer, nullable=False)
    interval_km: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
