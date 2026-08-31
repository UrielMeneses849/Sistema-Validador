from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    internal_number: Mapped[str] = mapped_column(String(60), unique=True, index=True, nullable=False)
    plate: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    vin: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    brand: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    current_odometer: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    maintenances: Mapped[List["Maintenance"]] = relationship(back_populates="vehicle")
    documents: Mapped[List["Document"]] = relationship(back_populates="vehicle")
    service_events: Mapped[List["ServiceEvent"]] = relationship(back_populates="vehicle")
    validations: Mapped[List["Validation"]] = relationship(back_populates="vehicle")
