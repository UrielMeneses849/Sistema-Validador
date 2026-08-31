from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str


@dataclass(frozen=True)
class ExtractionResult:
    method: str
    pages: list[ExtractedPage] = field(default_factory=list)
    has_usable_text: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text).strip()


class DocumentExtractor(Protocol):
    def extract(self, file_path: str, mime_type: str) -> ExtractionResult: ...


class PdfTextExtractor:
    """Lee la capa de texto de un PDF; nunca lo convierte a imagen."""

    minimum_useful_characters = 20

    def extract(self, file_path: str, mime_type: str) -> ExtractionResult:
        if mime_type != "application/pdf" and Path(file_path).suffix.lower() != ".pdf":
            return ExtractionResult(method="pdf_text", warnings=["El archivo no es un PDF."])
        try:
            from pypdf import PdfReader

            reader = PdfReader(file_path)
            pages = [
                ExtractedPage(page_number=index + 1, text=(page.extract_text() or "").strip())
                for index, page in enumerate(reader.pages)
            ]
        except Exception as exc:  # Un PDF malformado no debe impedir guardar el original.
            return ExtractionResult(method="pdf_text", warnings=[f"No fue posible leer el PDF: {exc}"])

        text = "".join(page.text for page in pages)
        usable_characters = sum(character.isalnum() for character in text)
        if usable_characters < self.minimum_useful_characters:
            return ExtractionResult(
                method="pdf_text",
                pages=pages,
                warnings=["El PDF no contiene una capa de texto útil; se requiere extractor visual."],
            )
        return ExtractionResult(method="pdf_text", pages=pages, has_usable_text=True)


class VisionExtractor:
    """Contrato para PDFs escaneados e imágenes; no acopla un proveedor todavía."""

    def extract(self, _file_path: str, _mime_type: str) -> ExtractionResult:
        return ExtractionResult(
            method="vision",
            warnings=["No hay un extractor visual configurado para este documento."],
        )


class OcrExtractor:
    """Extensión opcional futura. OCR no es requisito del flujo de PDF digital."""

    def extract(self, _file_path: str, _mime_type: str) -> ExtractionResult:
        return ExtractionResult(method="ocr", warnings=["OCR no está configurado."])


class ManualExtractor:
    """Fallback explícito: deja el documento disponible para corrección humana."""

    def extract(self, _file_path: str, _mime_type: str) -> ExtractionResult:
        return ExtractionResult(method="manual", warnings=["Captura manual disponible como fallback."])


class HybridExtractor:
    """Enruta por el tipo físico del archivo sin mezclarlo con reglas de negocio."""

    def __init__(
        self,
        pdf_text_extractor: DocumentExtractor | None = None,
        vision_extractor: DocumentExtractor | None = None,
        manual_extractor: DocumentExtractor | None = None,
    ) -> None:
        self.pdf_text_extractor = pdf_text_extractor or PdfTextExtractor()
        self.vision_extractor = vision_extractor or VisionExtractor()
        self.manual_extractor = manual_extractor or ManualExtractor()

    def extract(self, file_path: str, mime_type: str) -> ExtractionResult:
        suffix = Path(file_path).suffix.lower()
        if mime_type == "application/pdf" or suffix == ".pdf":
            pdf_result = self.pdf_text_extractor.extract(file_path, mime_type)
            if pdf_result.has_usable_text:
                return pdf_result
            visual_result = self.vision_extractor.extract(file_path, mime_type)
            return ExtractionResult(
                method=visual_result.method,
                pages=visual_result.pages,
                has_usable_text=visual_result.has_usable_text,
                warnings=[*pdf_result.warnings, *visual_result.warnings],
            )
        if mime_type in {"image/jpeg", "image/png"} or suffix in {".jpg", ".jpeg", ".png"}:
            return self.vision_extractor.extract(file_path, mime_type)
        return self.manual_extractor.extract(file_path, mime_type)
