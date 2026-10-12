from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.config import OCR_MAX_FILE_SIZE_MB
from app.services.extraction_service import HybridExtractor, OcrProcessingError, OcrUnavailableError


class InvalidContractDocumentError(ValueError):
    pass


class IncompleteContractError(ValueError):
    pass


@dataclass(frozen=True)
class ContractFields:
    numero_contrato: str
    fecha_factura_origen: date
    fecha_inicio_contrato: date
    fecha_fin_contrato: date
    initial_odometer: int
    brand: str
    model: str
    vehicle_condition: str
    extraction_method: str


DATE_TOKEN = r"(\d{1,2}[\/-]\d{1,2}[\/-]\d{4})"


def _search(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _normalized_text(text: str) -> str:
    # El OCR suele convertir Nº/1º y letras acentuadas en variantes distintas.
    # Una versión ASCII y con espacios compactados permite reconocerlas sin
    # depender del proveedor que produjo la capa de texto.
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).strip().upper()


def _parse_date(raw_value: str | None, field_label: str) -> date:
    if raw_value is None:
        raise IncompleteContractError(f"No se encontró {field_label} en el contrato.")
    normalized = raw_value.replace("-", "/")
    try:
        return datetime.strptime(normalized, "%d/%m/%Y").date()
    except ValueError as exc:
        raise IncompleteContractError(
            f"La {field_label} encontrada no es una fecha válida: {raw_value}."
        ) from exc


def _required_text(raw_value: str | None, field_label: str) -> str:
    value = re.sub(r"\s+", " ", raw_value or "").strip(" :-")
    if not value:
        raise IncompleteContractError(f"No se encontró {field_label} en el contrato.")
    return value


def _parse_mileage(raw_value: str | None) -> int:
    if raw_value is None:
        raise IncompleteContractError("No se encontró el kilometraje registrado en el contrato.")
    digits = re.sub(r"\D", "", raw_value)
    if not digits:
        raise IncompleteContractError("El kilometraje del contrato no contiene un valor válido.")
    return int(digits)


def parse_contract_text(text: str, *, extraction_method: str = "pdf_text") -> ContractFields:
    searchable = _normalized_text(text)
    contract_number = _search(
        r"(?:N(?:O|UMERO)?\.?\s*(?:DE\s+)?CONTRATO|CONTRATO\s*(?:NO|NUMERO))\s*[:#-]?\s*(\d{6})\b",
        searchable,
    )
    if contract_number is None:
        raise IncompleteContractError("No se encontró el número de contrato de seis dígitos.")

    invoice_date = _search(
        rf"(?:FECHA\s*(?:DE\s*)?)?1(?:O|A)?\s*FACTURA\s*[:#-]?\s*{DATE_TOKEN}",
        searchable,
    )
    start_date = _search(
        rf"FECHA\s*(?:DE\s*)?INICIO\s*(?:DE\s*)?(?:GARANTIA|CONTRATO)\s*[:#-]?\s*{DATE_TOKEN}",
        searchable,
    )
    end_date = _search(
        rf"FECHA\s*(?:DE\s*)?FIN\s*(?:DE\s*)?(?:GARANTIA|CONTRATO)\s*[:#-]?\s*{DATE_TOKEN}",
        searchable,
    )
    mileage = _search(
        r"(?:KILOMETROS|KILOMETRAJE|KMS?\.?)\s*[:#-]?\s*(\d{1,3}(?:[.,\s]\d{3})+|\d{1,7})\b",
        searchable,
    )
    brand = _search(
        r"\bMARCA\s+(.{1,60}?)\s+(?:(?:NUMERO|[A-Z]{2,5}\s+[A-Z]?MERO)\s+DE\s+SERIE|VIN\b)",
        searchable,
    )
    model = _search(
        r"\bMODELO\s+(.{1,80}?)\s+(?:FECHA\s+|[A-Z() -]{1,20}C\s+HA\s+)?1(?:O|A)?\s*FACTURA\b",
        searchable,
    )
    condition_code = _search(
        r"PRODUCTO\s+CONTRATADO.{0,120}?\b(M[12])\b",
        searchable,
    )

    parsed_start = _parse_date(start_date, "fecha de inicio de contrato")
    parsed_end = _parse_date(end_date, "fecha de fin de contrato")
    if parsed_end < parsed_start:
        raise IncompleteContractError(
            "La fecha de fin encontrada es anterior a la fecha de inicio del contrato."
        )

    parsed_brand = _required_text(brand, "la marca del vehículo")
    # Algunos contratos incluyen la carrocería junto a la marca (p. ej.
    # "CHEVROLET SUV"). Se elimina sólo ese sufijo para que coincida con el
    # catálogo de políticas de fabricante.
    parsed_brand = re.sub(
        r"\s+(?:SUV|SEDAN|PICK\s*UP|TT(?:\s+GM)?)$",
        "",
        parsed_brand,
    ).strip()
    parsed_model = _required_text(model, "el modelo del vehículo")
    # En algunas plantillas el rótulo FECHA se imprime encima del final del
    # modelo. Si deja un paréntesis abierto, se conserva la parte inequívoca.
    parsed_model = re.sub(r"\s+\([^)]*$", "", parsed_model).strip()
    if condition_code not in {"M1", "M2"}:
        raise IncompleteContractError("No se encontró la condición M1/M2 del contrato.")

    return ContractFields(
        numero_contrato=contract_number,
        fecha_factura_origen=_parse_date(invoice_date, "fecha de factura de origen"),
        fecha_inicio_contrato=parsed_start,
        fecha_fin_contrato=parsed_end,
        initial_odometer=_parse_mileage(mileage),
        brand=parsed_brand,
        model=parsed_model,
        vehicle_condition="new" if condition_code == "M1" else "used",
        extraction_method=extraction_method,
    )


def extract_contract_pdf(content: bytes, *, filename: str, content_type: str | None) -> ContractFields:
    suffix = Path(filename or "").suffix.lower()
    if suffix != ".pdf" or content_type not in {"application/pdf", "application/octet-stream", None, ""}:
        raise InvalidContractDocumentError("Selecciona un archivo PDF válido.")
    if not content:
        raise InvalidContractDocumentError("El contrato PDF está vacío.")
    if not content.startswith(b"%PDF"):
        raise InvalidContractDocumentError("El archivo seleccionado no contiene un PDF válido.")
    maximum_bytes = OCR_MAX_FILE_SIZE_MB * 1024 * 1024
    if len(content) > maximum_bytes:
        raise InvalidContractDocumentError(
            f"El contrato supera el límite de {OCR_MAX_FILE_SIZE_MB} MB."
        )

    # El contrato sólo vive en un directorio temporal durante la extracción.
    # No se crea Document, historial ni copia dentro de storage/documents.
    with TemporaryDirectory(prefix="contract-extraction-") as directory:
        temporary_pdf = Path(directory) / "contract.pdf"
        temporary_pdf.write_bytes(content)
        try:
            extraction = HybridExtractor().extract(str(temporary_pdf), "application/pdf")
        except (OcrUnavailableError, OcrProcessingError) as exc:
            raise IncompleteContractError(
                "No fue posible leer el contrato. Verifica que el PDF tenga texto legible."
            ) from exc

    if not extraction.text.strip():
        raise IncompleteContractError("No se encontró texto legible dentro del contrato PDF.")
    return parse_contract_text(extraction.text, extraction_method=extraction.method)
