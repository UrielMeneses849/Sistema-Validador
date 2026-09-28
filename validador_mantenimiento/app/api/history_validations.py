from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import OCR_MAX_IMAGES_PER_ANALYSIS
from app.database.database import get_db
from app.schemas.history_validation_schema import (
    HistoryValidationRead,
    HistoryValidationRequest,
)
from app.schemas.service_event_schema import ManualServiceEventCreate
from app.services.document_analysis_service import analyze_document
from app.services.document_service import InvalidDocumentError, save_document
from app.services.extraction_service import OcrProcessingError, OcrUnavailableError
from app.services.history_validation_service import (
    HistoryVehicleMismatchError,
    validate_maintenance_history,
)
from app.services.service_event_service import (
    ServiceEventNotFoundError,
    apply_vehicle_consistency_review,
    create_manual_service_event,
)
from app.services.vehicle_service import VehicleNotFoundError


router = APIRouter(prefix="/api/history-validations", tags=["Validación de historial"])


@router.post("/preview", response_model=HistoryValidationRead)
async def preview_history(
    vehicle_id: int = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> HistoryValidationRead:
    """Analiza un lote temporal y revierte todos sus registros al terminar."""
    if len(files) > OCR_MAX_IMAGES_PER_ANALYSIS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"El máximo es {OCR_MAX_IMAGES_PER_ANALYSIS} archivos por análisis.",
        )

    temporary_paths: list[Path] = []
    try:
        event_ids: list[int] = []
        document_ids: list[int] = []
        for uploaded_file in files:
            content = await uploaded_file.read()
            document = save_document(
                db,
                vehicle_id=vehicle_id,
                filename=uploaded_file.filename or "",
                content_type=uploaded_file.content_type,
                content=content,
                persist=False,
            )
            document_ids.append(document.id)
            temporary_paths.append(Path(document.file_path))
            analysis = analyze_document(
                db,
                document.id,
                persist=False,
                detect_duplicates=False,
                review_vehicle_history=False,
            )
            document_event_ids = [event.id for event in analysis.service_events]
            if not document_event_ids:
                fallback = create_manual_service_event(
                    db,
                    ManualServiceEventCreate(
                        document_id=document.id,
                        service_type="Mantenimiento preventivo",
                        description="Evento pendiente de confirmación manual",
                        resets_maintenance_interval=True,
                    ),
                    persist=False,
                )
                document_event_ids = [fallback.id]
            event_ids.extend(document_event_ids)

        scoped_event_ids = set(event_ids)
        apply_vehicle_consistency_review(db, vehicle_id, scoped_event_ids)
        result = validate_maintenance_history(
            db,
            HistoryValidationRequest(vehicle_id=vehicle_id, event_ids=event_ids),
            persist_validations=False,
            include_vehicle_history=False,
        )
        result.preview_document_ids = document_ids
        db.rollback()
        return result
    except VehicleNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except OcrUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except OcrProcessingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    finally:
        db.rollback()
        for path in temporary_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        for uploaded_file in files:
            await uploaded_file.close()


@router.post("", response_model=HistoryValidationRead, status_code=status.HTTP_201_CREATED)
def validate_history(
    payload: HistoryValidationRequest, db: Session = Depends(get_db)
) -> HistoryValidationRead:
    try:
        return validate_maintenance_history(db, payload)
    except (VehicleNotFoundError, ServiceEventNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except HistoryVehicleMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
