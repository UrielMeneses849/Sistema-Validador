from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.document_analysis_schema import DocumentAnalysisRead
from app.schemas.document_schema import DocumentRead
from app.services.document_analysis_service import (
    DocumentAnalysisReprocessConflict,
    analyze_document,
    get_document_analysis,
)
from app.services.document_service import (
    DocumentNotFoundError,
    InvalidDocumentError,
    get_document_or_raise,
    save_document,
)
from app.services.extraction_service import OcrProcessingError, OcrUnavailableError
from app.services.vehicle_service import VehicleNotFoundError


router = APIRouter(prefix="/api/documents", tags=["Documentos"])


@router.post("/upload", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload(
    vehicle_id: int = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db)
) -> DocumentRead:
    try:
        content = await file.read()
        return save_document(
            db,
            vehicle_id=vehicle_id,
            filename=file.filename or "",
            content_type=file.content_type,
            content=content,
        )
    except VehicleNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidDocumentError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    finally:
        await file.close()


@router.get("/{document_id}", response_model=DocumentRead)
def get_one(document_id: int, db: Session = Depends(get_db)) -> DocumentRead:
    try:
        return get_document_or_raise(db, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{document_id}/analyze", response_model=DocumentAnalysisRead)
def analyze(
    document_id: int,
    force: bool = Query(False, description="Reprocesa sólo resultados automáticos no confirmados."),
    db: Session = Depends(get_db),
) -> DocumentAnalysisRead:
    try:
        return analyze_document(db, document_id, force=force)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DocumentAnalysisReprocessConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except OcrUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except OcrProcessingError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.get("/{document_id}/analysis", response_model=DocumentAnalysisRead)
def get_analysis(document_id: int, db: Session = Depends(get_db)) -> DocumentAnalysisRead:
    try:
        get_document_or_raise(db, document_id)
        analysis = get_document_analysis(db, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if not analysis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El documento aún no ha sido analizado.")
    return analysis
