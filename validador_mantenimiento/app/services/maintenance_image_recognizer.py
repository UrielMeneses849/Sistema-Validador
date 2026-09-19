from __future__ import annotations

import base64
import io
import json
import os
import re
import select
import shutil
import subprocess
import time
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal, Protocol

from PIL import Image

from app.core.config import (
    EVIDENCE_CROP_DIR,
    HANDWRITING_PYTHON,
    HANDWRITING_TIMEOUT_SECONDS,
    HANDWRITING_MODEL_PATH,
    OCR_DEBUG,
    OCR_DEBUG_DIR,
    OCR_LANG,
    OCR_REVIEW_THRESHOLD,
    TESSERACT_CMD,
)
from app.services.ocr_debug_service import OcrDebugSession, box_payload
from app.services.image_preprocessing import ImageVariantSet, prepare_image_variants


FieldType = Literal["date", "mileage"]


class RecognizerUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class BoundingBox:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return max(0, self.x1 - self.x0)

    @property
    def height(self) -> int:
        return max(0, self.y1 - self.y0)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    def contains(self, other: "BoundingBox", margin: int = 0) -> bool:
        return (
            self.x0 - margin <= other.x0
            and self.y0 - margin <= other.y0
            and self.x1 + margin >= other.x1
            and self.y1 + margin >= other.y1
        )

    def clamp(self, width: int, height: int) -> "BoundingBox":
        return BoundingBox(
            max(0, min(self.x0, width - 1)),
            max(0, min(self.y0, height - 1)),
            max(1, min(self.x1, width)),
            max(1, min(self.y1, height)),
        )


@dataclass(frozen=True)
class OcrToken:
    text: str
    confidence: float
    box: BoundingBox
    label_type: FieldType | None = None


@dataclass(frozen=True)
class OcrReading:
    text: str
    confidence: float
    provider: str = "tesseract"
    metadata: dict[str, Any] = field(default_factory=dict)


class FieldOcrBackend(Protocol):
    name: str

    def detect_orientation(self, image: Image.Image) -> int | None: ...

    def read_layout(self, image: Image.Image, *, variant: str) -> list[OcrToken]: ...

    def read_field(self, image: Image.Image, *, field_type: FieldType, variant: str) -> OcrReading: ...


class MaintenanceImageRecognizer(Protocol):
    def analyze(self, image_path: str | Path) -> "RecognitionResult": ...


class ExternalVisionFallback(Protocol):
    """Contrato futuro. La implementación local nunca realiza HTTP ni usa proveedores externos."""

    def analyze(self, image_path: str | Path) -> "RecognitionResult": ...


@dataclass(frozen=True)
class RecognitionCandidate:
    raw_text: str
    normalized_value: str | int
    ocr_confidence: float
    format_confidence: float
    variant: str
    provider: str
    label_proximity: float = 1.0


@dataclass(frozen=True)
class RecognizedField:
    field_type: FieldType
    raw_value: str | None
    normalized_value: str | int | None
    confidence_score: float
    confidence: str
    ambiguous: bool
    crop_id: str | None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    selection_reason: str = ""


@dataclass(frozen=True)
class ServiceRegion:
    box: BoundingBox
    date_crop: BoundingBox
    mileage_crop: BoundingBox
    date_label: str = "Fecha"
    mileage_label: str = "Kilometraje"
    strategy: str = "generic_labels"


@dataclass(frozen=True)
class RecognizedServiceEvent:
    service_date: RecognizedField
    mileage: RecognizedField
    confidence_score: float
    requires_human_review: bool
    warnings: list[str]
    region: dict[str, int]
    region_index: int | None = None


@dataclass(frozen=True)
class RecognitionResult:
    events: list[RecognizedServiceEvent]
    provider: str
    diagnostics: list[str]
    external_fallback_used: bool = False
    debug_directory: str | None = None

    def metadata_events(self) -> list[dict[str, Any]]:
        return [asdict(event) for event in self.events]


class RecognitionPipeline:
    """Orquestador extensible; producción lo construye con optional_fallback=None."""

    def __init__(
        self,
        local_recognizer: MaintenanceImageRecognizer,
        optional_fallback: ExternalVisionFallback | None = None,
    ) -> None:
        self.local_recognizer = local_recognizer
        self.optional_fallback = optional_fallback

    def analyze(self, image_path: str | Path) -> RecognitionResult:
        local_result = self.local_recognizer.analyze(image_path)
        needs_review = not local_result.events or any(
            event.requires_human_review for event in local_result.events
        )
        if needs_review and self.optional_fallback is not None:
            return self.optional_fallback.analyze(image_path)
        return local_result


def _semantic(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.upper())
    return re.sub(
        r"[^A-Z0-9]+",
        " ",
        "".join(character for character in decomposed if unicodedata.category(character) != "Mn"),
    ).strip()


MONTHS = {
    "ENE": 1, "ENERO": 1, "JAN": 1, "JANUARY": 1,
    "FEB": 2, "FEBRERO": 2, "FEBRUARY": 2,
    "MAR": 3, "MARZO": 3, "MARCH": 3,
    "ABR": 4, "ABRIL": 4, "APR": 4, "APRIL": 4,
    "MAY": 5, "MAYO": 5,
    "JUN": 6, "JUNIO": 6, "JUNE": 6,
    "JUL": 7, "JULIO": 7, "JULY": 7,
    "AGO": 8, "AGOSTO": 8, "AUG": 8, "AUGUST": 8,
    "SEP": 9, "SEPT": 9, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "SEPTEMBER": 9,
    "OCT": 10, "OCTUBRE": 10, "OCTOBER": 10,
    "NOV": 11, "NOVIEMBRE": 11, "NOVEMBER": 11,
    "DIC": 12, "DICIEMBRE": 12, "DEC": 12, "DECEMBER": 12,
}


def _year(value: str) -> int | None:
    numeric = int(value)
    if len(value) == 2:
        numeric = 2000 + numeric if numeric <= 49 else 1900 + numeric
    return numeric if 1950 <= numeric <= date.today().year + 2 else None


def _safe_date(year: int | None, month: int, day: int) -> date | None:
    if year is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date_candidates(text: str) -> list[tuple[str, str, float]]:
    """Devuelve (ISO, texto, calidad de formato), sin inferir valores ausentes."""
    candidates: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()

    def add(raw: str, parsed: date | None, quality: float) -> None:
        if parsed is None:
            return
        item = (parsed.isoformat(), raw.strip())
        if item not in seen:
            seen.add(item)
            candidates.append((item[0], item[1], quality))

    for match in re.finditer(r"(?<!\d)((?:19|20)\d{2})[/.\-](0?[1-9]|1[0-2])[/.\-](0?[1-9]|[12]\d|3[01])(?!\d)", text):
        year, month, day = match.groups()
        add(match.group(0), _safe_date(int(year), int(month), int(day)), 1.0)
    for match in re.finditer(r"(?<!\d)(0?[1-9]|[12]\d|3[01])[/.\-](0?[1-9]|1[0-2])[/.\-]((?:19|20)?\d{2})(?!\d)", text):
        day, month, year = match.groups()
        add(match.group(0), _safe_date(_year(year), int(month), int(day)), 0.98 if len(year) == 4 else 0.88)

    comparable = _semantic(text)
    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    for match in re.finditer(
        rf"\b(0?[1-9]|[12]\d|3[01])(?:\s+DE)?\s+({month_names})(?:\s+DE)?\s+((?:19|20)?\d{{2}})\b",
        comparable,
    ):
        day, month_name, year = match.groups()
        add(match.group(0), _safe_date(_year(year), MONTHS[month_name], int(day)), 0.96 if len(year) == 4 else 0.86)
    for match in re.finditer(
        rf"\b({month_names})\s+(0?[1-9]|[12]\d|3[01])\s+((?:19|20)?\d{{2}})\b",
        comparable,
    ):
        month_name, day, year = match.groups()
        add(match.group(0), _safe_date(_year(year), MONTHS[month_name], int(day)), 0.94 if len(year) == 4 else 0.84)
    return candidates


def parse_mileage_candidates(text: str) -> list[tuple[int, str, float]]:
    candidates: list[tuple[int, str, float]] = []
    seen: set[tuple[int, str]] = set()
    pattern = re.compile(
        r"(?<![A-Z0-9?])(\d{1,3}(?:[,\.\s]\d{3})+|\d{1,8})(?:\s*(?:KM|KMS|KILOMETROS?|MILLAS?))?(?![A-Z0-9?])"
    )
    for match in pattern.finditer(text.upper()):
        raw = match.group(1).strip()
        digits = re.sub(r"\D", "", raw)
        if not digits:
            continue
        value = int(digits)
        if 1900 <= value <= 2100 and len(digits) == 4:
            continue
        quality = 1.0
        if len(digits) == 8 or value > 2_000_000:
            quality = 0.45
        elif len(digits) == 7:
            quality = 0.82
        elif len(digits) > 1 and digits.startswith("0"):
            quality = 0.62
        item = (value, raw)
        if item not in seen:
            seen.add(item)
            candidates.append((value, raw, quality))
    return candidates


def parse_trocr_mileage_candidate(text: str) -> list[tuple[int, str, float]]:
    """Tolera un sufijo alfabético corto de TrOCR, pero sólo si toda la salida es un número."""
    match = re.fullmatch(
        r"\s*(\d{2,3}(?:[,\.\s]\d{3})+|\d{2,7})\s*([A-Za-z]{1,3})?\s*\.?\s*",
        text,
    )
    if not match:
        return []
    digits = re.sub(r"\D", "", match.group(1))
    value = int(digits)
    if value > 2_000_000 or (1900 <= value <= 2100 and len(digits) == 4):
        return []
    # El sufijo es evidencia de alucinación; la calidad reducida y el límite TrOCR-only
    # garantizan revisión humana aun cuando varias variantes coincidan.
    quality = 0.72 if match.group(2) else 0.88
    return [(value, match.group(1), quality)]


def evaluate_consensus(
    field_type: FieldType,
    candidates: list[RecognitionCandidate],
    *,
    attempted_variants: int,
    crop_id: str | None = None,
) -> RecognizedField:
    """Combina variantes y motores sin aceptar silenciosamente un desacuerdo."""
    if not candidates:
        return RecognizedField(
            field_type, None, None, 0.0, "low", False, crop_id, [],
            "Ningún motor produjo un valor válido para el campo.",
        )
    grouped: dict[str | int, list[RecognitionCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.normalized_value].append(candidate)

    scored: list[tuple[float, str | int, list[RecognitionCandidate]]] = []
    for value, items in grouped.items():
        providers = {(item.variant, item.provider) for item in items}
        support = len(providers)
        agreement = min(1.0, support / min(3, max(1, attempted_variants)))
        ocr_confidence = sum(item.ocr_confidence for item in items) / len(items)
        format_confidence = max(item.format_confidence for item in items)
        proximity = max(item.label_proximity for item in items)
        score = 0.35 * ocr_confidence + 0.35 * agreement + 0.20 * format_confidence + 0.10 * proximity
        if support == 1:
            score = min(score, 0.79)
        scored.append((round(score, 4), value, items))
    scored.sort(key=lambda item: (item[0], len(item[2])), reverse=True)
    engine_values: dict[str, set[str | int]] = {"tesseract": set(), "trocr": set()}
    for candidate in candidates:
        engine = "trocr" if candidate.provider == "trocr_local" else "tesseract"
        engine_values[engine].add(candidate.normalized_value)

    both_engines = bool(engine_values["tesseract"] and engine_values["trocr"])
    common_values = engine_values["tesseract"] & engine_values["trocr"]
    if common_values:
        eligible = [item for item in scored if item[1] in common_values]
        best_score, best_value, best_items = eligible[0]
        best_score = min(0.99, best_score + 0.08)
        ambiguous = False
        selection_reason = "Tesseract y TrOCR coincidieron en el mismo valor válido."
    else:
        best_score, best_value, best_items = scored[0]
        close_alternative = bool(len(scored) > 1 and scored[1][0] >= best_score - 0.06)
        if both_engines:
            ambiguous = True
            best_score = min(best_score, 0.75)
            selection_reason = "Tesseract y TrOCR produjeron valores plausibles distintos; requiere revisión."
        elif engine_values["trocr"]:
            ambiguous = close_alternative
            best_score = min(best_score, 0.75 if ambiguous else 0.84)
            selection_reason = "Sólo TrOCR produjo un valor válido; se conserva con confianza limitada."
        else:
            ambiguous = close_alternative
            if ambiguous:
                best_score = min(best_score, 0.75)
                selection_reason = "Tesseract produjo alternativas cercanas; requiere revisión."
            else:
                selection_reason = "Sólo Tesseract produjo un valor válido; se aplicaron las reglas normales."
    confidence = "high" if best_score >= OCR_REVIEW_THRESHOLD else "medium" if best_score >= 0.60 else "low"
    public_candidates = []
    for score, value, items in scored:
        public_candidates.append({
            "normalized_value": value,
            "raw_value": items[0].raw_text,
            "confidence_score": score,
            "variants": sorted({item.variant for item in items}),
            "providers": sorted({item.provider for item in items}),
            "engines": sorted({"trocr" if item.provider == "trocr_local" else "tesseract" for item in items}),
            "support": len({(item.variant, item.provider) for item in items}),
            "selected": value == best_value,
        })
    return RecognizedField(
        field_type=field_type,
        raw_value=best_items[0].raw_text,
        normalized_value=best_value,
        confidence_score=round(best_score, 4),
        confidence=confidence,
        ambiguous=ambiguous,
        crop_id=crop_id,
        candidates=public_candidates,
        selection_reason=selection_reason,
    )


class TesseractFieldOcrBackend:
    name = "tesseract"

    def __init__(self, pytesseract_module, *, language: str = OCR_LANG) -> None:
        self.engine = pytesseract_module
        self.language = language

    def detect_orientation(self, image: Image.Image) -> int | None:
        try:
            output = self.engine.image_to_osd(image, output_type=self.engine.Output.DICT)
            rotation = int(output.get("rotate", 0) or 0)
            return rotation if rotation in {90, 180, 270} else None
        except Exception:
            return None

    def read_layout(self, image: Image.Image, *, variant: str) -> list[OcrToken]:
        data = self.engine.image_to_data(
            image, lang=self.language, config="--oem 3 --psm 11", output_type=self.engine.Output.DICT
        )
        tokens: list[OcrToken] = []
        for index, text in enumerate(data.get("text", [])):
            clean = str(text).strip()
            if not clean:
                continue
            try:
                confidence = max(0.0, min(1.0, float(data.get("conf", [0])[index]) / 100))
            except (ValueError, TypeError, IndexError):
                confidence = 0.0
            left = int(data.get("left", [0])[index] or 0)
            top = int(data.get("top", [0])[index] or 0)
            width = int(data.get("width", [0])[index] or 0)
            height = int(data.get("height", [0])[index] or 0)
            tokens.append(OcrToken(clean, confidence, BoundingBox(left, top, left + width, top + height)))
        return tokens

    def read_field(self, image: Image.Image, *, field_type: FieldType, variant: str) -> OcrReading:
        whitelist = (
            "0123456789., /-KkMm"
            if field_type == "mileage"
            else "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzáéíóúÁÉÍÓÚ., /-"
        )
        config = f"--oem 3 --psm 7 -c tessedit_char_whitelist={whitelist.replace(' ', '')}"
        data = self.engine.image_to_data(
            image, lang=self.language, config=config, output_type=self.engine.Output.DICT
        )
        texts: list[str] = []
        confidences: list[float] = []
        for index, value in enumerate(data.get("text", [])):
            clean = str(value).strip()
            if not clean:
                continue
            texts.append(clean)
            try:
                raw_confidence = float(data.get("conf", [0])[index])
                if raw_confidence >= 0:
                    confidences.append(max(0.0, min(1.0, raw_confidence / 100)))
            except (ValueError, TypeError, IndexError):
                pass
        return OcrReading(" ".join(texts), sum(confidences) / len(confidences) if confidences else 0.0)


class OptionalTrOcrFieldRecognizer:
    """Adaptador local/offline; ejecuta Torch fuera del entorno principal cuando se configura."""

    name = "trocr_local"

    def __init__(
        self,
        model_path: str | Path | None = HANDWRITING_MODEL_PATH,
        *,
        python_executable: str | Path | None = HANDWRITING_PYTHON,
        timeout_seconds: float = HANDWRITING_TIMEOUT_SECONDS,
    ) -> None:
        self.model_path = Path(model_path).expanduser() if model_path else None
        self.python_executable = Path(python_executable).expanduser() if python_executable else None
        self.timeout_seconds = timeout_seconds
        self._loaded: tuple[Any, Any, Any, str] | None = None
        self._worker: subprocess.Popen[str] | None = None
        self._worker_device: str | None = None
        self._model_load_seconds: float | None = None

    @property
    def configured(self) -> bool:
        return bool(self.model_path and self.model_path.is_dir())

    def diagnostic(self) -> str:
        if not self.model_path:
            return "TrOCR opcional desactivado: HANDWRITING_MODEL_PATH no está configurado."
        if not self.model_path.is_dir():
            return f"TrOCR opcional desactivado: no existe el modelo local {self.model_path}."
        if self.python_executable:
            if not self.python_executable.is_file():
                return f"TrOCR opcional desactivado: no existe el Python aislado {self.python_executable}."
            return f"TrOCR local disponible mediante el entorno aislado {self.python_executable}."
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError:
            return "TrOCR opcional desactivado: configura HANDWRITING_PYTHON con el entorno aislado."
        return "TrOCR local disponible en el proceso actual."

    def _read_worker_response(self) -> dict[str, Any]:
        if self._worker is None or self._worker.stdout is None:
            raise RecognizerUnavailableError("El proceso local de TrOCR no está activo.")
        ready, _, _ = select.select([self._worker.stdout], [], [], self.timeout_seconds)
        if not ready:
            self.close()
            raise RecognizerUnavailableError("TrOCR local excedió el tiempo máximo de respuesta.")
        line = self._worker.stdout.readline()
        if not line:
            code = self._worker.poll()
            self.close()
            raise RecognizerUnavailableError(f"El proceso local de TrOCR terminó inesperadamente ({code}).")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecognizerUnavailableError("TrOCR local devolvió una respuesta inválida.") from exc
        if payload.get("error"):
            raise RecognizerUnavailableError(f"TrOCR local: {payload['error']}")
        return payload

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.poll() is None:
            return
        if not self.configured or not self.python_executable or not self.python_executable.is_file():
            raise RecognizerUnavailableError(self.diagnostic())
        environment = os.environ.copy()
        environment.update({
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "TOKENIZERS_PARALLELISM": "false",
        })
        self._worker = subprocess.Popen(
            [
                str(self.python_executable), "-m", "training.trocr_inference_worker",
                "--model", str(self.model_path.resolve()),
            ],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        ready = self._read_worker_response()
        if not ready.get("ready"):
            self.close()
            raise RecognizerUnavailableError("TrOCR local no confirmó que el modelo estuviera listo.")
        self._worker_device = str(ready.get("device") or "unknown")
        self._model_load_seconds = float(ready.get("model_load_seconds") or 0.0)

    def _load(self):
        if self._loaded is not None:
            return self._loaded
        if not self.configured:
            raise RecognizerUnavailableError(self.diagnostic())
        try:
            import torch
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        except ImportError as exc:
            raise RecognizerUnavailableError(self.diagnostic()) from exc
        processor = TrOCRProcessor.from_pretrained(str(self.model_path), local_files_only=True)
        model = VisionEncoderDecoderModel.from_pretrained(str(self.model_path), local_files_only=True)
        device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
        model.to(device)
        model.eval()
        self._loaded = (torch, processor, model, device)
        return self._loaded

    def read_field(self, image: Image.Image, *, field_type: FieldType, variant: str) -> OcrReading:
        if self.python_executable:
            self._ensure_worker()
            if self._worker is None or self._worker.stdin is None:
                raise RecognizerUnavailableError("No se pudo iniciar el proceso local de TrOCR.")
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="PNG")
            request = {
                "image_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
                "field_type": field_type,
                "variant": variant,
            }
            self._worker.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            self._worker.stdin.flush()
            response = self._read_worker_response()
            return OcrReading(
                str(response.get("text") or "").strip(),
                max(0.0, min(1.0, float(response.get("confidence") or 0.0))),
                self.name,
                {
                    "device": response.get("device", self._worker_device),
                    "model_load_seconds": response.get("model_load_seconds", self._model_load_seconds),
                    "inference_seconds": response.get("inference_seconds"),
                    "token_count": response.get("token_count"),
                },
            )
        torch, processor, model, device = self._load()
        with torch.inference_mode():
            pixels = processor(images=image.convert("RGB"), return_tensors="pt").pixel_values.to(device)
            generated = model.generate(pixels, max_new_tokens=32)
        text = processor.batch_decode(generated, skip_special_tokens=True)[0]
        # Sin calibración propia no se interpreta la probabilidad generativa como certeza del campo.
        return OcrReading(text.strip(), 0.0, self.name, {"device": device, "confidence_calibrated": False})

    def close(self) -> None:
        worker, self._worker = self._worker, None
        if worker is None:
            return
        try:
            if worker.poll() is None and worker.stdin is not None:
                worker.stdin.write('{"command":"shutdown"}\n')
                worker.stdin.flush()
                worker.wait(timeout=5)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            if worker.poll() is None:
                worker.terminate()

    def __del__(self) -> None:
        self.close()


def _is_date_label(token: OcrToken) -> bool:
    value = _semantic(token.text)
    return value in {"FECHA", "DATE", "F SERVICIO", "F REPAR"} or value.startswith(("FECHA ", "DATE "))


def _is_mileage_label(token: OcrToken) -> bool:
    value = _semantic(token.text)
    return (
        value in {
            "KILOMETRAJE", "KILOMETROS", "ODOMETRO", "ODOMETER", "KMS", "KM", "MILLAS",
            "KM MILLAS", "KM KM",
        }
        or value.startswith(("KILOMETR", "ODOMETR", "KM ", "KMS "))
    )


def _merge_layout_tokens(layout_options: list[tuple[list[OcrToken], str]]) -> list[OcrToken]:
    """Une lecturas de layout con geometría común y prioriza etiquetas impresas."""
    merged: list[OcrToken] = []
    candidates: list[OcrToken] = []
    for tokens, _variant in layout_options:
        for token in tokens:
            label_type: FieldType | None = "date" if _is_date_label(token) else "mileage" if _is_mileage_label(token) else None
            candidates.append(OcrToken(token.text, token.confidence, token.box, label_type))

    for token in sorted(candidates, key=lambda item: (item.label_type is not None, item.confidence), reverse=True):
        semantic = _semantic(token.text)
        duplicate_index = next((
            index
            for index, prior in enumerate(merged)
            if (
                _semantic(prior.text) == semantic
                or (token.label_type is not None and token.label_type == prior.label_type)
            )
            and abs(prior.box.center[0] - token.box.center[0]) <= max(12, prior.box.width * 0.35)
            and abs(prior.box.center[1] - token.box.center[1]) <= max(18, prior.box.height * 0.75)
        ), None)
        if duplicate_index is None:
            merged.append(token)
        else:
            prior = merged[duplicate_index]
            preferred = token if token.confidence > prior.confidence else prior
            merged[duplicate_index] = OcrToken(
                preferred.text,
                max(prior.confidence, token.confidence),
                BoundingBox(
                    min(prior.box.x0, token.box.x0),
                    min(prior.box.y0, token.box.y0),
                    max(prior.box.x1, token.box.x1),
                    max(prior.box.y1, token.box.y1),
                ),
                preferred.label_type,
            )
    return sorted(merged, key=lambda item: (item.box.y0, item.box.x0))


def _detect_rectangles(image_path: Path) -> list[BoundingBox]:
    try:
        import cv2
    except ImportError:
        return []
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return []
    threshold = cv2.adaptiveThreshold(image, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12)
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, image.shape[1] // 35), 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, image.shape[0] // 35)))
    lines = cv2.bitwise_or(
        cv2.morphologyEx(threshold, cv2.MORPH_OPEN, horizontal_kernel),
        cv2.morphologyEx(threshold, cv2.MORPH_OPEN, vertical_kernel),
    )
    contours, _ = cv2.findContours(lines, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    page_area = image.shape[0] * image.shape[1]
    rectangles: list[BoundingBox] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area = width * height
        if area < page_area * 0.012 or area > page_area * 0.80 or width < 120 or height < 70:
            continue
        rectangles.append(BoundingBox(x, y, x + width, y + height))
    return sorted(rectangles, key=lambda box: (box.y0, box.x0, box.width * box.height))


def _region_ink_metrics(
    image_path: Path,
    crops: tuple[BoundingBox, BoundingBox],
) -> dict[str, Any] | None:
    """Distingue escritura/sello de una línea impresa vacía; sólo apoya revisión, nunca aceptación."""
    try:
        import cv2
    except ImportError:
        return None
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    residual_pixels = 0
    total_pixels = 0
    component_count = 0
    field_metrics: list[dict[str, Any]] = []
    for crop in crops:
        field = image[crop.y0:crop.y1, crop.x0:crop.x1]
        if field.size == 0:
            field_metrics.append({"ink_ratio": 0.0, "components": 0})
            continue
        inverted = cv2.threshold(field, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        field_height, field_width = inverted.shape
        horizontal = cv2.morphologyEx(
            inverted,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, field_width // 4), 1)),
        )
        vertical = cv2.morphologyEx(
            inverted,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, field_height // 2))),
        )
        residual = cv2.bitwise_and(inverted, cv2.bitwise_not(cv2.bitwise_or(horizontal, vertical)))
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(residual, 8)
        minimum_area = max(8, int(field.size * 0.0002))
        areas = stats[1:, cv2.CC_STAT_AREA]
        meaningful = areas[areas >= minimum_area]
        pixels = int(meaningful.sum()) if len(meaningful) else 0
        components = int(len(meaningful))
        residual_pixels += pixels
        total_pixels += int(field.size)
        component_count += components
        field_metrics.append({
            "ink_ratio": round(pixels / field.size, 4),
            "components": components,
        })
    combined_ratio = residual_pixels / max(total_pixels, 1)
    has_content = combined_ratio >= 0.025 or (combined_ratio >= 0.011 and component_count >= 18)
    return {
        "has_content": has_content,
        "combined_ink_ratio": round(combined_ratio, 4),
        "components": component_count,
        "fields": field_metrics,
    }


class GenericServiceBoxStrategy:
    name = "generic_labels"

    @staticmethod
    def _value_crop(
        label: OcrToken, box: BoundingBox, tokens: list[OcrToken], field_type: FieldType,
        width: int, height: int,
    ) -> BoundingBox:
        del tokens, field_type  # La geometría de la etiqueta manda; OCR preliminar no debe estrechar el crop.
        label_height = max(18, min(label.box.height, int(height * 0.025)))
        half_band = max(28, min(42, int(label_height * 1.15)))
        center_y = int(label.box.center[1])
        right_space = box.x1 - label.box.x1
        if right_space >= max(100, box.width * 0.22):
            return BoundingBox(
                label.box.x1 - 5, center_y - half_band,
                box.x1 - 4, center_y + half_band,
            ).clamp(width, height)
        return BoundingBox(
            box.x0 + 4, label.box.y1 - 4, box.x1 - 4,
            min(box.y1 - 4, label.box.y1 + max(70, label_height * 4)),
        ).clamp(width, height)

    def detect(
        self, tokens: list[OcrToken], *, width: int, height: int, rectangles: list[BoundingBox]
    ) -> list[ServiceRegion]:
        date_labels = sorted(
            [token for token in tokens if _is_date_label(token)],
            key=lambda item: (item.box.y0, item.box.x0),
        )
        mileage_labels = sorted(
            [token for token in tokens if _is_mileage_label(token)],
            key=lambda item: (item.box.y0, item.box.x0),
        )

        # Emparejamiento global y local: una etiqueta de otra columna nunca puede ser
        # apropiada sólo porque un rectángulo grande contenga toda la fila.
        pair_candidates: list[tuple[float, int, int]] = []
        for date_index, date_label in enumerate(date_labels):
            for mileage_index, mileage_label in enumerate(mileage_labels):
                dx = abs(date_label.box.center[0] - mileage_label.box.center[0])
                vertical_delta = mileage_label.box.center[1] - date_label.box.center[1]
                if (
                    dx <= max(150, width * 0.13)
                    and -max(12, height * 0.012) <= vertical_delta <= max(130, height * 0.12)
                ):
                    pair_candidates.append((dx + abs(vertical_delta) * 0.3, date_index, mileage_index))
        matched_dates: dict[int, int] = {}
        used_mileage: set[int] = set()
        for _score, date_index, mileage_index in sorted(pair_candidates):
            if date_index in matched_dates or mileage_index in used_mileage:
                continue
            matched_dates[date_index] = mileage_index
            used_mileage.add(mileage_index)

        def inferred_box(date_label: OcrToken, mileage_label: OcrToken) -> BoundingBox:
            containing = [
                rectangle for rectangle in rectangles
                if rectangle.contains(date_label.box, 4)
                and rectangle.contains(mileage_label.box, 4)
                and rectangle.width <= width * 0.43
                and rectangle.height <= height * 0.48
                and rectangle.width >= 180
                and rectangle.height >= 120
            ]
            if containing:
                return min(containing, key=lambda item: item.width * item.height)

            anchor_x = min(date_label.box.x0, mileage_label.box.x0)
            anchor_y = min(date_label.box.y0, mileage_label.box.y0)
            same_row_right = [
                other.box.x0
                for other in date_labels
                if other.box.x0 > anchor_x
                and abs(other.box.y0 - date_label.box.y0) <= max(55, height * 0.075)
            ]
            same_column_below = [
                other.box.y0
                for other in date_labels
                if other.box.y0 > date_label.box.y0
                and abs(other.box.x0 - date_label.box.x0) <= max(90, width * 0.085)
            ]
            x0 = max(0, anchor_x - max(20, int(width * 0.025)))
            x1 = (
                min(same_row_right) - max(8, int(width * 0.008))
                if same_row_right
                else min(width, x0 + max(260, int(width * 0.30)))
            )
            y0 = max(0, anchor_y - max(45, int(height * 0.16)))
            y1 = (
                min(same_column_below) - max(45, int(height * 0.15))
                if same_column_below
                else min(height, max(date_label.box.y1, mileage_label.box.y1) + max(90, int(height * 0.12)))
            )
            return BoundingBox(
                x0,
                y0,
                max(x0 + 80, x1),
                max(y0 + 80, y1),
            ).clamp(width, height)

        regions: list[ServiceRegion] = []
        for date_index, date_label in enumerate(date_labels):
            mileage_index = matched_dates.get(date_index)
            if mileage_index is not None:
                mileage_label = mileage_labels[mileage_index]
                strategy = self.name
            else:
                label_height = max(18, date_label.box.height)
                inferred_y = date_label.box.y0 + max(40, int(label_height * 1.8))
                mileage_label = OcrToken(
                    "Km/Millas (inferida)",
                    0.0,
                    BoundingBox(
                        date_label.box.x0,
                        inferred_y,
                        date_label.box.x0 + max(85, int(date_label.box.width * 1.3)),
                        inferred_y + label_height,
                    ).clamp(width, height),
                    "mileage",
                )
                strategy = f"{self.name}:date_label_only"
            box = inferred_box(date_label, mileage_label)
            regions.append(ServiceRegion(
                box=box,
                date_crop=self._value_crop(date_label, box, tokens, "date", width, height),
                mileage_crop=self._value_crop(mileage_label, box, tokens, "mileage", width, height),
                date_label=date_label.text,
                mileage_label=mileage_label.text,
                strategy=strategy,
            ))
        # Contornos anidados y OCR de dos variantes pueden producir duplicados.
        unique: list[ServiceRegion] = []
        row_band = max(1, int(height * 0.12))
        for region in sorted(regions, key=lambda item: (int(item.date_crop.center[1]) // row_band, item.box.x0)):
            if any(
                abs(region.date_crop.center[0] - prior.date_crop.center[0]) < 30
                and abs(region.date_crop.center[1] - prior.date_crop.center[1]) < 30
                for prior in unique
            ):
                continue
            unique.append(region)
        return unique


class LocalMaintenanceImageRecognizer:
    """Pipeline especializado fecha+km. No transcribe ni envía la página a servicios externos."""

    recognition_variants = ("original", "grayscale", "high_contrast", "adaptive_threshold", "shadow_normalized", "blue_reduced")
    handwriting_variants = ("original", "grayscale", "high_contrast")

    def __init__(
        self,
        ocr_backend: FieldOcrBackend,
        *,
        handwriting_recognizer: OptionalTrOcrFieldRecognizer | None = None,
        external_fallback: ExternalVisionFallback | None = None,
        evidence_directory: str | Path = EVIDENCE_CROP_DIR,
        debug_enabled: bool = OCR_DEBUG,
        debug_directory: str | Path = OCR_DEBUG_DIR,
        debug_run_id: str | None = None,
    ) -> None:
        self.ocr_backend = ocr_backend
        self.handwriting_recognizer = handwriting_recognizer or OptionalTrOcrFieldRecognizer()
        self.external_fallback = external_fallback  # En esta versión debe permanecer None.
        self.evidence_directory = Path(evidence_directory)
        self.debug_enabled = debug_enabled
        self.debug_directory = Path(debug_directory)
        self.debug_run_id = debug_run_id
        self.strategy = GenericServiceBoxStrategy()

    def _collect_candidates(
        self,
        variants: ImageVariantSet,
        crop: BoundingBox,
        field_type: FieldType,
    ) -> tuple[list[RecognitionCandidate], list[dict[str, Any]]]:
        candidates: list[RecognitionCandidate] = []
        attempts: list[dict[str, Any]] = []
        parser = parse_date_candidates if field_type == "date" else parse_mileage_candidates
        for variant in self.recognition_variants:
            path = variants.variants.get(variant)
            if path is None:
                continue
            started = time.perf_counter()
            with Image.open(path) as image:
                reading = self.ocr_backend.read_field(image.crop((crop.x0, crop.y0, crop.x1, crop.y1)), field_type=field_type, variant=variant)
            wall_seconds = time.perf_counter() - started
            parsed = parser(reading.text)
            attempts.append({
                "variant": variant,
                "provider": reading.provider,
                "raw_text": reading.text,
                "ocr_confidence": round(reading.confidence, 4),
                "runtime": {**reading.metadata, "wall_seconds": round(wall_seconds, 4)},
                "parsed_candidates": [
                    {"normalized_value": normalized, "raw_value": raw, "format_confidence": format_confidence}
                    for normalized, raw, format_confidence in parsed
                ],
            })
            for normalized, raw, format_confidence in parsed:
                candidates.append(RecognitionCandidate(
                    raw_text=raw,
                    normalized_value=normalized,
                    ocr_confidence=reading.confidence,
                    format_confidence=format_confidence,
                    variant=variant,
                    provider=reading.provider,
                ))
        if self.handwriting_recognizer.configured:
            for variant in self.handwriting_variants:
                path = variants.variants.get(variant)
                if path is None:
                    continue
                try:
                    started = time.perf_counter()
                    with Image.open(path) as image:
                        crop_image = image.crop((crop.x0, crop.y0, crop.x1, crop.y1))
                        reading = self.handwriting_recognizer.read_field(
                            crop_image, field_type=field_type, variant=variant
                        )
                    wall_seconds = time.perf_counter() - started
                    parsed = (
                        parse_trocr_mileage_candidate(reading.text)
                        if field_type == "mileage"
                        else parser(reading.text)
                    )
                    attempts.append({
                        "variant": variant,
                        "provider": reading.provider,
                        "raw_text": reading.text,
                        "ocr_confidence": round(reading.confidence, 4),
                        "runtime": {**reading.metadata, "wall_seconds": round(wall_seconds, 4)},
                        "parsed_candidates": [
                            {"normalized_value": normalized, "raw_value": raw, "format_confidence": format_confidence}
                            for normalized, raw, format_confidence in parsed
                        ],
                    })
                    for normalized, raw, format_confidence in parsed:
                        candidates.append(RecognitionCandidate(
                            raw, normalized, reading.confidence, format_confidence,
                            variant, reading.provider,
                        ))
                except RecognizerUnavailableError as exc:
                    attempts.append({
                        "variant": variant,
                        "provider": self.handwriting_recognizer.name,
                        "error": str(exc),
                        "parsed_candidates": [],
                    })
                    break
        return candidates, attempts

    @staticmethod
    def _decision_payload(
        recognized: RecognizedField,
        attempts: list[dict[str, Any]],
        *,
        requires_human_review: bool,
    ) -> dict[str, Any]:
        def attempts_for(provider: str) -> list[dict[str, Any]]:
            return [item for item in attempts if item.get("provider") == provider]

        return {
            "tesseract_candidates": attempts_for("tesseract")
            or [item for item in attempts if item.get("provider") != "trocr_local"],
            "trocr_candidates": attempts_for("trocr_local"),
            "selected_candidate": recognized.normalized_value,
            "selected_raw_value": recognized.raw_value,
            "selection_reason": recognized.selection_reason,
            "confidence_score": recognized.confidence_score,
            "confidence": recognized.confidence,
            "ambiguous": recognized.ambiguous,
            "requires_human_review": requires_human_review,
        }

    def _save_crop(
        self, variants: ImageVariantSet, crop: BoundingBox, source_key: str, event_index: int, field_type: FieldType
    ) -> str:
        relative = Path(source_key) / f"service-{event_index:03d}-{field_type}.png"
        destination = self.evidence_directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(variants.variants["original"]) as original:
            original.crop((crop.x0, crop.y0, crop.x1, crop.y1)).save(destination, format="PNG", optimize=True)
        return relative.as_posix()

    def analyze(self, image_path: str | Path) -> RecognitionResult:
        source = Path(image_path)
        source_key = re.sub(r"[^A-Za-z0-9_.-]", "_", source.stem)[:80] or "image"
        debug = OcrDebugSession(
            enabled=self.debug_enabled,
            base_directory=self.debug_directory,
            run_id=self.debug_run_id or source_key,
        )
        diagnostics = [self.handwriting_recognizer.diagnostic(), "Fallback externo desactivado."]
        events: list[RecognizedServiceEvent] = []
        debug_regions: list[dict[str, Any]] = []
        event_report_indices: list[int] = []
        with TemporaryDirectory(prefix="maintenance-image-") as temporary:
            variants = prepare_image_variants(
                source,
                temporary,
                orientation_detector=self.ocr_backend.detect_orientation,
            )
            diagnostics.extend(variants.diagnostics)
            debug.initialize(source, variants)
            layout_options: list[tuple[list[OcrToken], str]] = []
            for variant in ("high_contrast", "grayscale", "original"):
                with Image.open(variants.variants[variant]) as image:
                    variant_tokens = self.ocr_backend.read_layout(image, variant=variant)
                layout_options.append((variant_tokens, variant))
            tokens = _merge_layout_tokens(layout_options)
            rectangles = _detect_rectangles(variants.variants["adaptive_threshold"])
            regions = self.strategy.detect(tokens, width=variants.width, height=variants.height, rectangles=rectangles)
            variant_counts = ", ".join(
                f"{variant}={len(variant_tokens)}"
                for variant_tokens, variant in layout_options
            )
            diagnostics.append(
                f"Layout combinado ({variant_counts}): {len(tokens)} tokens únicos, "
                f"{len(rectangles)} rectángulos y {len(regions)} cuadros candidatos."
            )
            debug.capture_layout(
                variants,
                tokens=tokens,
                rectangles=rectangles,
                regions=regions,
                layout_variants={variant: variant_tokens for variant_tokens, variant in layout_options},
            )
            for region_index, region in enumerate(regions, start=1):
                debug.capture_region(variants, index=region_index, region=region)
                date_candidates, date_attempts = self._collect_candidates(variants, region.date_crop, "date")
                mileage_candidates, mileage_attempts = self._collect_candidates(variants, region.mileage_crop, "mileage")
                ink_metrics = _region_ink_metrics(
                    variants.variants["original"],
                    (region.date_crop, region.mileage_crop),
                )
                report = {
                    "region_index": region_index,
                    "strategy": region.strategy,
                    "service_box": box_payload(region.box),
                    "date_crop": box_payload(region.date_crop),
                    "mileage_crop": box_payload(region.mileage_crop),
                    "date_label": region.date_label,
                    "mileage_label": region.mileage_label,
                    "ocr_attempts": {"date": date_attempts, "mileage": mileage_attempts},
                    "ink_metrics": ink_metrics,
                    "emitted_event": False,
                }
                debug_regions.append(report)
                # Un cuadro con tinta pero sin lectura válida se conserva para revisión humana.
                # El detector de tinta jamás aumenta la confianza ni acepta un valor.
                has_visual_content = bool(ink_metrics and ink_metrics["has_content"])
                if not date_candidates and not mileage_candidates and not has_visual_content:
                    empty_date = evaluate_consensus(
                        "date", date_candidates, attempted_variants=len(self.recognition_variants)
                    )
                    empty_mileage = evaluate_consensus(
                        "mileage", mileage_candidates, attempted_variants=len(self.recognition_variants)
                    )
                    report["field_decisions"] = {
                        "date": self._decision_payload(empty_date, date_attempts, requires_human_review=False),
                        "mileage": self._decision_payload(empty_mileage, mileage_attempts, requires_human_review=False),
                    }
                    continue
                date_crop_id = self._save_crop(variants, region.date_crop, source_key, region_index, "date")
                mileage_crop_id = self._save_crop(variants, region.mileage_crop, source_key, region_index, "mileage")
                recognized_date = evaluate_consensus(
                    "date", date_candidates, attempted_variants=len(self.recognition_variants), crop_id=date_crop_id
                )
                recognized_mileage = evaluate_consensus(
                    "mileage", mileage_candidates, attempted_variants=len(self.recognition_variants), crop_id=mileage_crop_id
                )
                warnings: list[str] = []
                if recognized_date.normalized_value is None:
                    warnings.append("No se detectó una fecha de servicio en el recorte especializado.")
                elif date.fromisoformat(str(recognized_date.normalized_value)) > date.today() + timedelta(days=31):
                    warnings.append("La fecha detectada es futura y requiere confirmación.")
                if recognized_mileage.normalized_value is None:
                    warnings.append("No se detectó kilometraje en el recorte especializado.")
                elif int(recognized_mileage.normalized_value) > 2_000_000:
                    warnings.append("El kilometraje detectado es improbable (más de 2,000,000 km).")
                pair_score = min(recognized_date.confidence_score, recognized_mileage.confidence_score)
                requires_review = (
                    pair_score < OCR_REVIEW_THRESHOLD
                    or recognized_date.ambiguous
                    or recognized_mileage.ambiguous
                    or bool(warnings)
                )
                event = RecognizedServiceEvent(
                    service_date=recognized_date,
                    mileage=recognized_mileage,
                    confidence_score=round(pair_score, 4),
                    requires_human_review=requires_review,
                    warnings=warnings,
                    region=asdict(region.box),
                    region_index=region_index,
                )
                events.append(event)
                report["emitted_event"] = True
                report["event_index"] = len(events)
                report["initial_result"] = asdict(event)
                report["field_decisions"] = {
                    "date": self._decision_payload(
                        recognized_date, date_attempts, requires_human_review=requires_review
                    ),
                    "mileage": self._decision_payload(
                        recognized_mileage, mileage_attempts, requires_human_review=requires_review
                    ),
                }
                event_report_indices.append(len(debug_regions) - 1)

        if self.handwriting_recognizer._worker_device:
            diagnostics.append(
                "TrOCR local ejecutado en "
                f"{self.handwriting_recognizer._worker_device}; carga del modelo: "
                f"{self.handwriting_recognizer._model_load_seconds or 0.0:.4f} s."
            )
        self.handwriting_recognizer.close()

        final_events = list(events)
        previous_layout_date: date | None = None
        for event_index, event in enumerate(events):
            current_layout_date = (
                date.fromisoformat(str(event.service_date.normalized_value))
                if event.service_date.normalized_value is not None
                else None
            )
            if (
                current_layout_date is not None
                and previous_layout_date is not None
                and current_layout_date < previous_layout_date
            ):
                final_events[event_index] = RecognizedServiceEvent(
                    service_date=event.service_date,
                    mileage=event.mileage,
                    confidence_score=event.confidence_score,
                    requires_human_review=True,
                    warnings=[
                        *event.warnings,
                        "La fecha disminuye respecto al cuadro de servicio anterior en la página.",
                    ],
                    region=event.region,
                    region_index=event.region_index,
                )
            if current_layout_date is not None:
                previous_layout_date = current_layout_date

        ordered = sorted(
            enumerate(final_events),
            key=lambda item: (
                item[1].service_date.normalized_value is None,
                str(item[1].service_date.normalized_value or "9999-12-31"),
                item[0],
            ),
        )
        previous_mileage: int | None = None
        for original_index, event in ordered:
            current = int(event.mileage.normalized_value) if event.mileage.normalized_value is not None else None
            if current is not None and previous_mileage is not None and current < previous_mileage:
                final_events[original_index] = RecognizedServiceEvent(
                    service_date=event.service_date,
                    mileage=event.mileage,
                    confidence_score=event.confidence_score,
                    requires_human_review=True,
                    warnings=[*event.warnings, "El kilometraje disminuye respecto al servicio cronológicamente anterior."],
                    region=event.region,
                    region_index=event.region_index,
                )
            if current is not None:
                previous_mileage = current

        for event_index, report_index in enumerate(event_report_indices):
            debug_regions[report_index]["final_result"] = asdict(final_events[event_index])
        debug.write_report({
            "source": str(source.resolve()),
            "provider": (
                f"{self.ocr_backend.name}+{self.handwriting_recognizer.name}"
                if any(
                    attempt.get("provider") == self.handwriting_recognizer.name
                    for region in debug_regions
                    for field_attempts in region.get("ocr_attempts", {}).values()
                    for attempt in field_attempts
                    if not attempt.get("error")
                )
                else self.ocr_backend.name
            ),
            "boxes_detected": len(debug_regions),
            "detected_events": len(final_events),
            "regions": debug_regions,
            "diagnostics": diagnostics,
            "external_fallback_used": False,
        })
        if debug.public_path:
            diagnostics.append(f"Depuración OCR guardada en {debug.public_path}.")
        result = RecognitionResult(
            final_events,
            (
                f"{self.ocr_backend.name}+{self.handwriting_recognizer.name}"
                if self.handwriting_recognizer._worker_device
                else self.ocr_backend.name
            ),
            diagnostics,
            debug_directory=debug.public_path,
        )
        # Contrato listo, pero deliberadamente no se llama al fallback en esta versión.
        return result


def local_ocr_diagnostics() -> dict[str, Any]:
    """Diagnóstico sin cargar modelos ni efectuar descargas o solicitudes de red."""
    return {
        "tesseract_executable": shutil.which(TESSERACT_CMD or "tesseract"),
        "opencv_available": _module_available("cv2"),
        "pytesseract_available": _module_available("pytesseract"),
        "trocr": OptionalTrOcrFieldRecognizer().diagnostic(),
        "external_fallback": "disabled",
        "network_required_at_runtime": False,
        "debug_enabled": OCR_DEBUG,
        "debug_directory": str(OCR_DEBUG_DIR.resolve()) if OCR_DEBUG else None,
    }


def _module_available(name: str) -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False
