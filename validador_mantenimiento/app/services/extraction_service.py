from __future__ import annotations

import shutil
import re
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from app.core.config import (
    HANDWRITING_MODEL_PATH,
    HANDWRITING_PYTHON,
    OCR_DEBUG,
    OCR_DEBUG_DIR,
    OCR_LANG,
    OCR_PROVIDER,
    OCR_REVIEW_THRESHOLD,
    TESSERACT_CMD,
)
from app.services.image_preprocessing import ImageValidationError, preprocess_image
from app.services.maintenance_image_recognizer import (
    LocalMaintenanceImageRecognizer,
    OptionalTrOcrFieldRecognizer,
    RecognitionPipeline,
    RecognizerUnavailableError,
    TesseractFieldOcrBackend,
)


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str


@dataclass(frozen=True)
class LayoutWord:
    """Token extraído y su posición; OCR agrega confianza normalizada de 0 a 1."""

    text: str
    page_number: int
    x0: float
    x1: float
    top: float
    bottom: float
    confidence: float | None = None


@dataclass(frozen=True)
class ExtractionResult:
    method: str
    pages: list[ExtractedPage] = field(default_factory=list)
    words: list[LayoutWord] = field(default_factory=list)
    has_usable_text: bool = False
    warnings: list[str] = field(default_factory=list)
    language: str | None = None
    configuration: str | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text).strip()


class DocumentExtractor(Protocol):
    def extract(self, file_path: str, mime_type: str) -> ExtractionResult: ...


class OcrUnavailableError(RuntimeError):
    """El proveedor local no está instalado o carece de los idiomas requeridos."""


class OcrProcessingError(RuntimeError):
    """El motor existe, pero no pudo procesar una imagen válida."""


class PdfTextExtractor:
    """Lee texto y coordenadas nativas con pdfplumber, sin rasterizar."""

    minimum_useful_characters = 20
    minimum_recognizable_words = 5

    def extract(self, file_path: str, mime_type: str) -> ExtractionResult:
        if mime_type != "application/pdf" and Path(file_path).suffix.lower() != ".pdf":
            return ExtractionResult(method="pdf_text", warnings=["El archivo no es un PDF."])
        try:
            import pdfplumber

            pages: list[ExtractedPage] = []
            words: list[LayoutWord] = []
            with pdfplumber.open(file_path) as pdf:
                for page_number, page in enumerate(pdf.pages, start=1):
                    pages.append(
                        ExtractedPage(
                            page_number=page_number,
                            text=(page.extract_text(x_tolerance=1, y_tolerance=2) or "").strip(),
                        )
                    )
                    for word in page.extract_words(x_tolerance=1, y_tolerance=2, keep_blank_chars=False):
                        words.append(
                            LayoutWord(
                                text=word["text"],
                                page_number=page_number,
                                x0=float(word["x0"]),
                                x1=float(word["x1"]),
                                top=float(word["top"]),
                                bottom=float(word["bottom"]),
                            )
                        )
        except Exception as exc:  # Un PDF malformado no debe impedir guardar el original.
            return ExtractionResult(method="pdf_text", warnings=[f"No fue posible leer el PDF: {exc}"])

        text = "\n".join(page.text for page in pages)
        usable_characters = sum(character.isalnum() for character in text)
        tokens = re.findall(r"\b[\wÁÉÍÓÚÜÑáéíóúüñ.-]+\b", text)
        recognizable_words = [token for token in tokens if len(token.strip(".-")) >= 2 and any(char.isalpha() for char in token)]
        nonempty_lines = [line for line in text.splitlines() if len(line.strip()) >= 3]
        distinct_tops = {
            (word.page_number, round(word.top / 4))
            for word in words
            if word.text.strip()
        }
        recognizable_ratio = len(recognizable_words) / max(len(tokens), 1)
        has_distribution = len(nonempty_lines) >= 2 and len(distinct_tops) >= 2
        useful = (
            usable_characters >= self.minimum_useful_characters
            and len(recognizable_words) >= self.minimum_recognizable_words
            and recognizable_ratio >= 0.30
            and has_distribution
        )
        quality_score = min(
            1.0,
            0.25 * min(usable_characters / 100, 1)
            + 0.30 * min(len(recognizable_words) / 20, 1)
            + 0.25 * min(recognizable_ratio / 0.65, 1)
            + 0.20 * (1 if has_distribution else 0),
        )
        metadata = {
            "provider": "pdfplumber",
            "extraction_confidence": round(quality_score, 4),
            "usable_characters": usable_characters,
            "token_count": len(tokens),
            "recognizable_word_count": len(recognizable_words),
            "recognizable_word_ratio": round(recognizable_ratio, 4),
            "distributed_lines": len(distinct_tops),
        }
        if not useful:
            return ExtractionResult(
                method="pdf_text",
                pages=pages,
                words=words,
                warnings=[
                    "La capa de texto nativa es insuficiente o está mal distribuida; se requiere OCR."
                ],
                metadata=metadata,
            )
        return ExtractionResult(
            method="pdf_text",
            pages=pages,
            words=words,
            has_usable_text=True,
            metadata=metadata,
        )


class OcrExtractor:
    """Proveedor OCR local basado en Tesseract, desacoplado del parser de documentos."""

    tesseract_config = "--oem 3 --psm 6"

    def __init__(
        self,
        *,
        language: str = OCR_LANG,
        tesseract_cmd: str | None = TESSERACT_CMD,
        debug_enabled: bool = OCR_DEBUG,
        debug_directory: str | Path = OCR_DEBUG_DIR,
        debug_run_id: str | None = None,
        handwriting_model_path: str | Path | None = HANDWRITING_MODEL_PATH,
        handwriting_python: str | Path | None = HANDWRITING_PYTHON,
    ) -> None:
        self.language = language
        self.tesseract_cmd = tesseract_cmd
        self.debug_enabled = debug_enabled
        self.debug_directory = Path(debug_directory)
        self.debug_run_id = debug_run_id
        self.handwriting_model_path = handwriting_model_path
        self.handwriting_python = handwriting_python

    def _load_engine(self):
        executable = shutil.which(self.tesseract_cmd or "tesseract")
        if not executable:
            raise OcrUnavailableError(
                "Tesseract OCR no está disponible. Instala Tesseract con los idiomas "
                f"requeridos ({self.language}) o configura TESSERACT_CMD."
            )
        try:
            import pytesseract
        except ImportError as exc:
            raise OcrUnavailableError(
                "Falta el paquete Python pytesseract. Instala las dependencias de requirements.txt."
            ) from exc
        pytesseract.pytesseract.tesseract_cmd = executable
        return pytesseract

    @staticmethod
    def _words_and_pages(data: dict) -> tuple[list[LayoutWord], list[ExtractedPage]]:
        words: list[LayoutWord] = []
        lines: dict[tuple[int, int, int, int], list[str]] = {}
        count = len(data.get("text", []))
        for index in range(count):
            text = str(data["text"][index]).strip()
            if not text:
                continue
            try:
                raw_confidence = float(data.get("conf", ["-1"] * count)[index])
            except (TypeError, ValueError):
                raw_confidence = -1
            confidence = max(0.0, min(1.0, raw_confidence / 100)) if raw_confidence >= 0 else None
            page_number = int(data.get("page_num", [1] * count)[index] or 1)
            left = float(data.get("left", [0] * count)[index] or 0)
            top = float(data.get("top", [0] * count)[index] or 0)
            width = float(data.get("width", [0] * count)[index] or 0)
            height = float(data.get("height", [0] * count)[index] or 0)
            words.append(LayoutWord(text, page_number, left, left + width, top, top + height, confidence))
            line_key = (
                page_number,
                int(data.get("block_num", [0] * count)[index] or 0),
                int(data.get("par_num", [0] * count)[index] or 0),
                int(data.get("line_num", [0] * count)[index] or 0),
            )
            lines.setdefault(line_key, []).append(text)
        page_lines: dict[int, list[str]] = {}
        for (page_number, _, _, _), tokens in lines.items():
            page_lines.setdefault(page_number, []).append(" ".join(tokens))
        pages = [ExtractedPage(page, "\n".join(page_lines[page])) for page in sorted(page_lines)]
        return words, pages

    def extract(self, file_path: str, mime_type: str) -> ExtractionResult:
        suffix = Path(file_path).suffix.lower()
        is_pdf = mime_type == "application/pdf" or suffix == ".pdf"
        is_image = mime_type in {"image/jpeg", "image/png", "image/webp", "application/octet-stream"} or suffix in {".jpg", ".jpeg", ".png", ".webp"}
        if not is_pdf and not is_image:
            return ExtractionResult(
                method="ocr",
                warnings=["El OCR local sólo procesa PDF, JPEG, PNG o WEBP."],
                language=self.language,
                configuration=self.tesseract_config,
            )
        pytesseract = self._load_engine()
        if is_image:
            handwriting_recognizer = OptionalTrOcrFieldRecognizer(
                self.handwriting_model_path,
                python_executable=self.handwriting_python,
            )
            try:
                recognition = RecognitionPipeline(
                    LocalMaintenanceImageRecognizer(
                        TesseractFieldOcrBackend(pytesseract, language=self.language),
                        handwriting_recognizer=handwriting_recognizer,
                        debug_enabled=self.debug_enabled,
                        debug_directory=self.debug_directory,
                        debug_run_id=self.debug_run_id,
                    ),
                    optional_fallback=None,
                ).analyze(file_path)
            except RecognizerUnavailableError as exc:
                raise OcrUnavailableError(str(exc)) from exc
            except ImageValidationError as exc:
                raise OcrProcessingError(str(exc)) from exc
            except pytesseract.TesseractError as exc:
                message = str(exc)
                if "Failed loading language" in message or "Error opening data file" in message:
                    raise OcrUnavailableError(
                        f"Tesseract no pudo cargar los idiomas {self.language}. Instala sus datos de idioma."
                    ) from exc
                raise OcrProcessingError("Tesseract no pudo procesar la imagen especializada.") from exc
            finally:
                handwriting_recognizer.close()

            lines: list[str] = []
            warnings: list[str] = []
            for event in recognition.events:
                date_text = event.service_date.raw_value or "fecha no detectada"
                mileage_text = event.mileage.raw_value or "kilometraje no detectado"
                lines.append(
                    f"MANTENIMIENTO\nFecha: {date_text}\nKilometraje: {mileage_text} km"
                )
                warnings.extend(event.warnings)
            if not recognition.events:
                warnings.append(
                    "No se encontraron cuadros con fecha y kilometraje; se requiere captura manual."
                )
            elif any(event.requires_human_review for event in recognition.events):
                warnings.append("Uno o más mantenimientos requieren revisión humana.")
            return ExtractionResult(
                method="local_maintenance_image",
                pages=[ExtractedPage(1, "\n\n".join(lines))] if lines else [],
                has_usable_text=bool(recognition.events),
                warnings=list(dict.fromkeys(warnings)),
                language=self.language,
                configuration="specialized-fields:date+mileage",
                metadata={
                    "provider": recognition.provider,
                    "pipeline": "maintenance_image_v1",
                    "event_count": len(recognition.events),
                    "maintenance_image_events": recognition.metadata_events(),
                    "diagnostics": recognition.diagnostics,
                    "external_fallback_used": recognition.external_fallback_used,
                    "external_fallback_configured": False,
                    "debug_directory": recognition.debug_directory,
                },
            )
        try:
            with TemporaryDirectory(prefix="maintenance-ocr-") as temporary_directory:
                sources: list[Path] = []
                if is_pdf:
                    try:
                        import pypdfium2 as pdfium
                    except ImportError as exc:
                        raise OcrUnavailableError(
                            "Falta pypdfium2 para convertir páginas escaneadas antes de OCR."
                        ) from exc
                    pdf = pdfium.PdfDocument(file_path)
                    try:
                        for page_index in range(len(pdf)):
                            rendered_path = Path(temporary_directory) / f"page-{page_index + 1}.png"
                            page = pdf[page_index]
                            bitmap = page.render(scale=2.5)
                            bitmap.to_pil().save(rendered_path, format="PNG")
                            sources.append(rendered_path)
                    finally:
                        pdf.close()
                else:
                    sources = [Path(file_path)]

                words: list[LayoutWord] = []
                pages: list[ExtractedPage] = []
                for page_number, source in enumerate(sources, start=1):
                    prepared_path = Path(temporary_directory) / f"prepared-{page_number}.png"
                    preprocess_image(source, prepared_path)
                    data = pytesseract.image_to_data(
                        str(prepared_path),
                        lang=self.language,
                        config=self.tesseract_config,
                        output_type=pytesseract.Output.DICT,
                    )
                    data["page_num"] = [page_number] * len(data.get("text", []))
                    page_words, page_texts = self._words_and_pages(data)
                    words.extend(page_words)
                    pages.extend(page_texts)
        except ImageValidationError as exc:
            raise OcrProcessingError(str(exc)) from exc
        except pytesseract.TesseractNotFoundError as exc:
            raise OcrUnavailableError(
                "Tesseract OCR no está disponible. Instálalo o configura TESSERACT_CMD."
            ) from exc
        except pytesseract.TesseractError as exc:
            message = str(exc)
            if "Failed loading language" in message or "Error opening data file" in message:
                raise OcrUnavailableError(
                    f"Tesseract no pudo cargar los idiomas {self.language}. Instala sus datos de idioma."
                ) from exc
            raise OcrProcessingError("Tesseract no pudo procesar la imagen.") from exc

        text = "\n".join(page.text for page in pages)
        usable_characters = sum(character.isalnum() for character in text)
        token_confidences = [word.confidence for word in words if word.confidence is not None]
        average_confidence = sum(token_confidences) / len(token_confidences) if token_confidences else 0.0
        warnings: list[str] = []
        if usable_characters < 8:
            warnings.append("Tesseract no encontró texto suficiente en la imagen.")
        if token_confidences and average_confidence < OCR_REVIEW_THRESHOLD:
            warnings.append("La confianza promedio del OCR está por debajo del umbral de revisión.")
        return ExtractionResult(
            method="ocr",
            pages=pages,
            words=words,
            has_usable_text=usable_characters >= 8,
            warnings=warnings,
            language=self.language,
            configuration=self.tesseract_config,
            metadata={
                "provider": "tesseract",
                "language": self.language,
                "configuration": self.tesseract_config,
                "token_count": len(words),
                "average_token_confidence": round(average_confidence, 4),
            },
        )


class VisionExtractor:
    """Punto de extensión visual; hoy delega en el proveedor OCR configurado."""

    def __init__(
        self,
        ocr_extractor: DocumentExtractor | None = None,
        *,
        debug_run_id: str | None = None,
    ) -> None:
        if ocr_extractor is not None:
            self.ocr_extractor = ocr_extractor
        elif OCR_PROVIDER == "tesseract":
            self.ocr_extractor = OcrExtractor(debug_run_id=debug_run_id)
        else:
            self.ocr_extractor = None

    def extract(self, file_path: str, mime_type: str) -> ExtractionResult:
        if self.ocr_extractor is None:
            raise OcrUnavailableError(
                f"El proveedor OCR '{OCR_PROVIDER}' no está soportado. Usa OCR_PROVIDER=tesseract."
            )
        return self.ocr_extractor.extract(file_path, mime_type)


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
        *,
        debug_run_id: str | None = None,
    ) -> None:
        self.pdf_text_extractor = pdf_text_extractor or PdfTextExtractor()
        self.vision_extractor = vision_extractor or VisionExtractor(debug_run_id=debug_run_id)
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
                words=visual_result.words,
                has_usable_text=visual_result.has_usable_text,
                warnings=[*pdf_result.warnings, *visual_result.warnings],
                language=visual_result.language,
                configuration=visual_result.configuration,
                metadata=visual_result.metadata,
            )
        if mime_type in {"image/jpeg", "image/png", "image/webp"} or suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            return self.vision_extractor.extract(file_path, mime_type)
        return self.manual_extractor.extract(file_path, mime_type)
