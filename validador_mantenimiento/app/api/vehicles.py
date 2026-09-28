from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.vehicle_schema import (
    ContractExtractionRead,
    NextContractRead,
    VehicleCreate,
    VehicleRead,
    VehicleUpdate,
)
from app.services.contract_extraction_service import (
    IncompleteContractError,
    InvalidContractDocumentError,
    extract_contract_pdf,
)
from app.services.vehicle_service import (
    ContractDateRangeError,
    ContractNumberImmutableError,
    ContractNumberUnavailableError,
    DuplicateInternalNumberError,
    InitialOdometerImmutableError,
    VehicleBusinessDataError,
    VehicleDeletionError,
    VehicleNotFoundError,
    create_vehicle,
    delete_vehicle,
    get_vehicle_or_raise,
    get_next_contract_number,
    list_vehicles,
    update_vehicle,
)


router = APIRouter(prefix="/api/vehicles", tags=["Vehículos"])


def _not_found(error: VehicleNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))


@router.post("", response_model=VehicleRead, status_code=status.HTTP_201_CREATED)
def create(payload: VehicleCreate, db: Session = Depends(get_db)) -> VehicleRead:
    try:
        return create_vehicle(db, payload)
    except ContractNumberUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DuplicateInternalNumberError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("", response_model=list[VehicleRead])
def get_all(
    search: Optional[str] = Query(default=None, max_length=100),
    status_filter: Optional[str] = Query(default=None, alias="status", pattern="^(active|inactive)$"),
    db: Session = Depends(get_db),
) -> list[VehicleRead]:
    return list_vehicles(db, search=search, status=status_filter)


@router.get("/next-contract", response_model=NextContractRead)
def get_next_contract(db: Session = Depends(get_db)) -> NextContractRead:
    """Previsualiza el consecutivo; POST sigue siendo quien lo asigna."""
    try:
        return NextContractRead(numero_contrato=get_next_contract_number(db))
    except ContractNumberUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/extract-contract", response_model=ContractExtractionRead)
async def extract_contract(file: UploadFile = File(...)) -> ContractExtractionRead:
    """Extrae el alta desde un PDF temporal; no registra documentos ni historial."""
    content = await file.read()
    try:
        result = extract_contract_pdf(
            content,
            filename=file.filename or "",
            content_type=file.content_type,
        )
    except InvalidContractDocumentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except IncompleteContractError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return ContractExtractionRead(**result.__dict__)


@router.get("/{vehicle_id}", response_model=VehicleRead)
def get_one(vehicle_id: int, db: Session = Depends(get_db)) -> VehicleRead:
    try:
        return get_vehicle_or_raise(db, vehicle_id)
    except VehicleNotFoundError as exc:
        raise _not_found(exc) from exc


@router.put("/{vehicle_id}", response_model=VehicleRead)
def update(vehicle_id: int, payload: VehicleUpdate, db: Session = Depends(get_db)) -> VehicleRead:
    try:
        return update_vehicle(db, vehicle_id, payload)
    except VehicleNotFoundError as exc:
        raise _not_found(exc) from exc
    except (
        ContractNumberImmutableError,
        ContractDateRangeError,
        InitialOdometerImmutableError,
        VehicleBusinessDataError,
    ) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except DuplicateInternalNumberError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(vehicle_id: int, db: Session = Depends(get_db)) -> None:
    """Elimina el vehículo, su historial relacionado y sus archivos originales."""
    try:
        delete_vehicle(db, vehicle_id)
    except VehicleNotFoundError as exc:
        raise _not_found(exc) from exc
    except VehicleDeletionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
