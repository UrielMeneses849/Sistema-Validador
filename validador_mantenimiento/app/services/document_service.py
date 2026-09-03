from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import (
    ALLOWED_EXTENSIONS,
    ALLOWED_MIME_TYPES,
    OCR_MAX_FILE_SIZE_MB,
    STORAGE_DIR,
)
from app.models.document import Document
from app.services.image_preprocessing import ImageValidationError, SUPPORTED_IMAGE_FORMATS, inspect_image_content
from app.services.vehicle_service import get_vehicle_or_raise


class InvalidDocumentError(Exception):
    pass


class DocumentNotFoundError(Exception):
    pass


def _validate_document(filename: str, content_type: str | None, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise InvalidDocumentError("El archivo debe ser PDF, JPG, JPEG, PNG o WEBP.")
    if not content:
        raise InvalidDocumentError("El archivo está vacío.")
    if content_type and content_type not in ALLOWED_MIME_TYPES:
        raise InvalidDocumentError("El tipo de archivo no está permitido.")
    if suffix != ".pdf":
        maximum_bytes = OCR_MAX_FILE_SIZE_MB * 1024 * 1024
        if len(content) > maximum_bytes:
            raise InvalidDocumentError(f"La imagen supera el límite de {OCR_MAX_FILE_SIZE_MB} MB.")
        try:
            image_format, _, _ = inspect_image_content(content)
        except ImageValidationError as exc:
            raise InvalidDocumentError(str(exc)) from exc
        expected_suffixes = {image_suffix for image_name, image_suffix in SUPPORTED_IMAGE_FORMATS.items() if image_name == image_format}
        if image_format == "JPEG":
            expected_suffixes.add(".jpeg")
        if suffix not in expected_suffixes:
            raise InvalidDocumentError("La extensión no coincide con el contenido real de la imagen.")
        expected_mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[image_format]
        if content_type not in (None, "application/octet-stream", expected_mime):
            raise InvalidDocumentError("El tipo MIME no coincide con el contenido real de la imagen.")
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
