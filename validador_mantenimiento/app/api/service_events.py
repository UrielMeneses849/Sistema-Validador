from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.service_event_schema import ManualServiceEventCreate, ServiceEventRead, ServiceEventUpdate
from app.schemas.validation_schema import ValidationRead
from app.services.document_service import DocumentNotFoundError
from app.services.service_event_service import (
    ServiceEventNotFoundError,
    confirm_service_event,
    create_manual_service_event,
    get_service_event_or_raise,
    update_service_event,
)
from app.services.training_dataset_service import resolve_evidence_crop
from app.services.validation_service import validate_service_event


router = APIRouter(prefix="/api/service-events", tags=["Eventos de servicio"])


@router.post("", response_model=ServiceEventRead, status_code=201)
def create_manual(payload: ManualServiceEventCreate, db: Session = Depends(get_db)) -> ServiceEventRead:
    try:
        return create_manual_service_event(db, payload)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{event_id}", response_model=ServiceEventRead)
def get_one(event_id: int, db: Session = Depends(get_db)) -> ServiceEventRead:
    try:
        return ServiceEventRead.model_validate(get_service_event_or_raise(db, event_id))
    except ServiceEventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{event_id}", response_model=ServiceEventRead)
def update(event_id: int, payload: ServiceEventUpdate, db: Session = Depends(get_db)) -> ServiceEventRead:
    try:
        return update_service_event(db, event_id, payload)
    except ServiceEventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{event_id}/confirm", response_model=ServiceEventRead)
def confirm(event_id: int, db: Session = Depends(get_db)) -> ServiceEventRead:
    try:
        return confirm_service_event(db, event_id)
    except ServiceEventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{event_id}/evidence/{field_name}", response_class=FileResponse)
def evidence_crop(event_id: int, field_name: str, db: Session = Depends(get_db)) -> FileResponse:
    if field_name not in {"date", "mileage_km"}:
        raise HTTPException(status_code=404, detail="Campo de evidencia no disponible.")
    try:
        event = get_service_event_or_raise(db, event_id)
    except ServiceEventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    field = (event.field_evidence or {}).get(field_name)
    crop_id = field.get("crop_id") if isinstance(field, dict) else None
    try:
        path = resolve_evidence_crop(str(crop_id or ""))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="El evento no tiene recorte para este campo.") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="El recorte de evidencia no está disponible.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.post("/{event_id}/validate", response_model=ValidationRead)
def validate(event_id: int, db: Session = Depends(get_db)) -> ValidationRead:
    try:
        return validate_service_event(db, event_id)
    except ServiceEventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
