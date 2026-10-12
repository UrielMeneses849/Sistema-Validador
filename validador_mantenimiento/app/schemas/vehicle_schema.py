from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class VehicleCreate(BaseModel):
    """Acepta las altas históricas y la nueva captura basada en contrato."""

    # Campos heredados para no romper vehículos y flujos existentes.
    internal_number: Optional[str] = Field(default=None, min_length=1, max_length=60)
    plate: Optional[str] = Field(default=None, min_length=1, max_length=30)
    vin: Optional[str] = Field(default=None, max_length=80)
    brand: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=80)
    year: Optional[int] = Field(default=None, ge=1886, le=2100)
    current_odometer: Optional[int] = Field(default=None, ge=0)
    status: Literal["active", "inactive"] = "active"

    # Campos del formulario de contrato.
    numero_contrato: Optional[str] = Field(default=None, pattern=r"^\d{6}$")
    kilometraje: Optional[int] = Field(default=None, ge=0)
    fecha_factura_origen: Optional[date] = None
    fecha_inicio_contrato: Optional[date] = None
    fecha_fin_contrato: Optional[date] = None
    vehicle_condition: Literal["new", "used", "unknown"] = "unknown"
    initial_odometer: Optional[int] = Field(default=None, ge=0)

    @field_validator("internal_number", "plate", "brand", "model", mode="before")
    @classmethod
    def strip_text_values(cls, value: Optional[str]) -> Optional[str]:
        value = value.strip() if isinstance(value, str) else value
        if value == "":
            raise ValueError("Este campo no puede estar vacío.")
        return value

    @field_validator("vin", mode="before")
    @classmethod
    def normalize_vin(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("numero_contrato", mode="before")
    @classmethod
    def normalize_contract_number(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_registration_shape(self) -> "VehicleCreate":
        contract_registration = any(
            value is not None
            for value in (
                self.numero_contrato,
                self.kilometraje,
                self.fecha_factura_origen,
                self.fecha_inicio_contrato,
                self.fecha_fin_contrato,
            )
        )
        if contract_registration:
            contract_fields = {
                "kilometraje": self.kilometraje,
                "fecha_factura_origen": self.fecha_factura_origen,
                "fecha_inicio_contrato": self.fecha_inicio_contrato,
                "fecha_fin_contrato": self.fecha_fin_contrato,
            }
            missing = [name for name, value in contract_fields.items() if value is None]
            if missing:
                raise ValueError(f"Faltan campos obligatorios del contrato: {', '.join(missing)}.")
            if self.fecha_fin_contrato < self.fecha_inicio_contrato:
                raise ValueError("La fecha de fin de contrato no puede ser anterior a la fecha de inicio.")
        else:
            legacy_fields = {
                "internal_number": self.internal_number,
                "plate": self.plate,
                "year": self.year,
                "current_odometer": self.current_odometer,
            }
            missing = [name for name, value in legacy_fields.items() if value is None]
            if missing:
                raise ValueError(f"Faltan campos obligatorios del vehículo: {', '.join(missing)}.")
        if self.vehicle_condition in {"new", "used"} and self.initial_odometer is None:
            raise ValueError("El kilometraje al inicio del contrato es obligatorio.")
        return self


class VehicleUpdate(BaseModel):
    internal_number: Optional[str] = Field(default=None, min_length=1, max_length=60)
    plate: Optional[str] = Field(default=None, min_length=1, max_length=30)
    vin: Optional[str] = Field(default=None, max_length=80)
    brand: Optional[str] = Field(default=None, min_length=1, max_length=80)
    model: Optional[str] = Field(default=None, min_length=1, max_length=80)
    year: Optional[int] = Field(default=None, ge=1886, le=2100)
    current_odometer: Optional[int] = Field(default=None, ge=0)
    status: Optional[Literal["active", "inactive"]] = None
    numero_contrato: Optional[str] = Field(default=None, pattern=r"^\d{6}$")
    kilometraje: Optional[int] = Field(default=None, ge=0)
    fecha_factura_origen: Optional[date] = None
    fecha_inicio_contrato: Optional[date] = None
    fecha_fin_contrato: Optional[date] = None
    vehicle_condition: Optional[Literal["new", "used", "unknown"]] = None
    initial_odometer: Optional[int] = Field(default=None, ge=0)

    @field_validator("internal_number", "plate", "brand", "model", mode="before")
    @classmethod
    def strip_optional_text_values(cls, value: Optional[str]) -> Optional[str]:
        value = value.strip() if isinstance(value, str) else value
        if value == "":
            raise ValueError("Este campo no puede estar vacío.")
        return value

    @field_validator("vin", mode="before")
    @classmethod
    def normalize_optional_vin(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("numero_contrato", mode="before")
    @classmethod
    def normalize_optional_contract_number(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_date_range(self) -> "VehicleUpdate":
        if (
            self.fecha_inicio_contrato is not None
            and self.fecha_fin_contrato is not None
            and self.fecha_fin_contrato < self.fecha_inicio_contrato
        ):
            raise ValueError("La fecha de fin de contrato no puede ser anterior a la fecha de inicio.")
        return self


class VehicleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    internal_number: Optional[str]
    plate: Optional[str]
    vin: Optional[str]
    brand: str
    model: str
    year: Optional[int]
    current_odometer: int
    status: Literal["active", "inactive"]
    numero_contrato: Optional[str]
    kilometraje: Optional[int]
    fecha_factura_origen: Optional[date]
    fecha_inicio_contrato: Optional[date]
    fecha_fin_contrato: Optional[date]
    vehicle_condition: Literal["new", "used", "unknown"]
    initial_odometer: Optional[int]
    created_at: datetime
    updated_at: datetime


class NextContractRead(BaseModel):
    numero_contrato: str = Field(pattern=r"^\d{6}$")


class ContractExtractionRead(BaseModel):
    """Campos de alta obtenidos de un contrato PDF sin persistir el archivo."""

    numero_contrato: str = Field(pattern=r"^\d{6}$")
    fecha_factura_origen: date
    fecha_inicio_contrato: date
    fecha_fin_contrato: date
    initial_odometer: int = Field(ge=0)
    brand: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=80)
    vehicle_condition: Literal["new", "used"]
    extraction_method: str
