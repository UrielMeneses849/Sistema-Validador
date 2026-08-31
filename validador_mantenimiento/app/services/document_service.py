from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import ALLOWED_EXTENSIONS, ALLOWED_MIME_TYPES, STORAGE_DIR
from app.models.document import Document
from app.services.vehicle_service import get_vehicle_or_raise


class InvalidDocumentError(Exception):
    pass


class DocumentNotFoundError(Exception):
    pass


def _validate_document(filename: str, content_type: str | None, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise InvalidDocumentError("El archivo debe ser PDF, JPG, JPEG o PNG.")
    if not content:
        raise InvalidDocumentError("El archivo está vacío.")
    if content_type and content_type not in ALLOWED_MIME_TYPES:
        raise InvalidDocumentError("El tipo de archivo no está permitido.")
    return suffix


def save_document(
    db: Session, *, vehicle_id: int, filename: str, content_type: str | None, content: bytes
) -> Document:
    get_vehicle_or_raise(db, vehicle_id)
    original_filename = Path(filename or "").name
    suffix = _validate_document(original_filename, content_type, content)
    stored_filename = f"{uuid4().hex}{suffix}"
    destination = STORAGE_DIR / stored_filename
    try:
        destination.write_bytes(content)
    except OSError as exc:
        raise InvalidDocumentError("No fue posible guardar el archivo original.") from exc

    document = Document(
        vehicle_id=vehicle_id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        file_path=str(destination),
        mime_type=content_type or "application/octet-stream",
        file_size=len(content),
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def get_document_or_raise(db: Session, document_id: int) -> Document:
    document = db.get(Document, document_id)
    if not document:
        raise DocumentNotFoundError(f"No existe el documento con ID {document_id}.")
    return document


def is_document_available(document: Document) -> bool:
    return document.file_size > 0 and Path(document.file_path).is_file() and Path(document.file_path).stat().st_size > 0

