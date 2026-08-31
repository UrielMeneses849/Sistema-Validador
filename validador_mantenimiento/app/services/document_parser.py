from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Optional

from app.services.extraction_service import ExtractionResult


DATE_PATTERN = re.compile(r"\b(0?[1-9]|[12]\d|3[01])[/-](0?[1-9]|1[0-2])[/-]((?:19|20)?\d{2})\b")
VIN_PATTERN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
PLATE_PATTERN = re.compile(r"\b[A-Z]{3}\d{3}[A-Z]?\b", re.IGNORECASE)
MILEAGE_PATTERN = re.compile(r"(?<![A-Z0-9])(?:\d{1,3}(?:[,\.\s]\d{3})+|\d{4,7})(?![A-Z0-9])")

# Alias de fabricante, independientes del nombre o los valores de un archivo.
BRAND_ALIASES = {"CHEVROLET": "CHEVROLET", "CHEVY": "CHEVROLET", "GM": "CHEVROLET", "FORD": "FORD", "KIA": "KIA", "HYUNDAI": "HYUNDAI", "NISSAN": "NISSAN", "TOYOTA": "TOYOTA"}


@dataclass(frozen=True)
class FieldValue:
    raw_value: Optional[str] = None
    normalized_value: Any = None
    confidence: str = "low"
    source_page: Optional[int] = None

    def evidence(self, method: str) -> dict[str, Any]:
        normalized = self.normalized_value.isoformat() if isinstance(self.normalized_value, date) else self.normalized_value
        return {
            "raw_value": self.raw_value,
            "normalized_value": normalized,
            "source_page": self.source_page,
            "extraction_method": method,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ParsedServiceEvent:
    service_date: FieldValue
    mileage: FieldValue
    repair_order_number: FieldValue
    dealer: FieldValue
    service_category: str
    service_type: Optional[str]
    description: Optional[str]
    resets_maintenance_interval: Optional[bool]
    confidence: str
    requires_human_review: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedDocument:
    document_type: str
    confidence: str
    fields: dict[str, FieldValue]
    service_events: list[ParsedServiceEvent]
    warnings: list[str] = field(default_factory=list)


def _comparison_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.upper())
    return "".join(character for character in normalized if unicodedata.category(character) != "Mn")


def _confidence(score: int, occurrences: int = 1) -> str:
    if score >= 9 or occurrences >= 2:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


def _iter_matches(pages: Iterable[tuple[int, str]], pattern: re.Pattern[str]):
    for page_number, page_text in pages:
        for match in pattern.finditer(page_text):
            yield page_number, page_text, match


def _choose_by_frequency(matches: list[tuple[int, str]], normalizer) -> FieldValue:
    if not matches:
        return FieldValue()
    normalized = [(page, raw, normalizer(raw)) for page, raw in matches]
    values = Counter(value for _, _, value in normalized if value is not None)
    if not values:
        return FieldValue()
    selected, occurrences = values.most_common(1)[0]
    page, raw, _ = next(item for item in normalized if item[2] == selected)
    return FieldValue(raw_value=raw, normalized_value=selected, confidence=_confidence(occurrences * 2, occurrences), source_page=page)


def _normalize_date(raw: str) -> Optional[date]:
    match = DATE_PATTERN.fullmatch(raw.strip())
    if not match:
        return None
    day, month, year = match.groups()
    normalized_year = int(year) + 2000 if len(year) == 2 else int(year)
    try:
        return datetime(normalized_year, int(month), int(day)).date()
    except ValueError:
        return None


def extract_date(pages: list[tuple[int, str]]) -> FieldValue:
    matches = [(page, match.group(0)) for page, _, match in _iter_matches(pages, DATE_PATTERN)]
    return _choose_by_frequency(matches, _normalize_date)


def _normalize_mileage(raw: str) -> Optional[int]:
    digits = re.sub(r"[^0-9]", "", raw)
    if not digits:
        return None
    value = int(digits)
    return value if 0 <= value <= 2_000_000 else None


def extract_mileage(pages: list[tuple[int, str]]) -> FieldValue:
    candidates: list[tuple[int, str, int]] = []
    scores: Counter[int] = Counter()
    raw_for_value: dict[int, tuple[int, str]] = {}
    for page_number, page_text, match in _iter_matches(pages, MILEAGE_PATTERN):
        raw = match.group(0)
        value = _normalize_mileage(raw)
        if value is None or 1900 <= value <= 2100:
            continue  # Años no son odómetros.
        start, end = match.span()
        context = _comparison_text(page_text[max(0, start - 70) : min(len(page_text), end + 70)])
        score = 1
        if raw.lstrip().startswith("0") and len(re.sub(r"\D", "", raw)) >= 6:
            score -= 6  # Códigos de operación/refacción suelen tener ceros iniciales.
        if re.search(r"\b(KILOMETRAJE|ODOMETRO|KM\.?\s*(ENTRADA|ENT\.?|ACTUAL)?)\b", context):
            score += 8
        if DATE_PATTERN.search(page_text[end : min(len(page_text), end + 30)]):
            score += 4
        if VIN_PATTERN.search(page_text[end : min(len(page_text), end + 100)]):
            score += 4
        if re.search(r"\b(SERVICIO|OPERACION|PARTE|COSTO|TOTAL|IVA|SUBTOTAL)\b", context):
            score -= 4
        candidates.append((page_number, raw, value))
        scores[value] += score
        raw_for_value.setdefault(value, (page_number, raw))
    if not candidates:
        return FieldValue()
    selected = max(
        scores,
        key=lambda value: (scores[value], sum(item[2] == value for item in candidates), -next(index for index, item in enumerate(candidates) if item[2] == value)),
    )
    page, raw = raw_for_value[selected]
    occurrences = sum(item[2] == selected for item in candidates)
    return FieldValue(raw_value=raw, normalized_value=selected, confidence=_confidence(scores[selected], occurrences), source_page=page)


def extract_vin(pages: list[tuple[int, str]]) -> FieldValue:
    matches = [(page, match.group(0).upper()) for page, _, match in _iter_matches(pages, VIN_PATTERN)]
    result = _choose_by_frequency(matches, lambda value: value.upper())
    return FieldValue(result.raw_value, result.normalized_value, "high" if result.normalized_value else "low", result.source_page)


def extract_plates(pages: list[tuple[int, str]]) -> FieldValue:
    matches = [(page, match.group(0).upper()) for page, _, match in _iter_matches(pages, PLATE_PATTERN)]
    result = _choose_by_frequency(matches, lambda value: value.upper())
    return FieldValue(result.raw_value, result.normalized_value, "medium" if result.normalized_value else "low", result.source_page)


def extract_vehicle(pages: list[tuple[int, str]]) -> dict[str, FieldValue]:
    joined = "\n".join(text for _, text in pages)
    comparable = _comparison_text(joined)
    brand = next((canonical for token, canonical in BRAND_ALIASES.items() if re.search(rf"\b{token}\b", comparable)), None)
    model_match = re.search(r"(?:19|20)\d{2}\s*([A-Z]{3,}?)(?=\d{3,7}\s+[A-HJ-NPR-Z0-9]{17})", comparable)
    labeled_model = re.search(r"\b(?:VEHICULO|MODELO|LINEA DE AUTO)\s*:?\s*([A-Z]{3,})\b", comparable)
    model = model_match.group(1) if model_match else labeled_model.group(1) if labeled_model else None
    year_match = re.search(r"\b((?:19|20)\d{2})\s*(?:" + (model or "[A-Z]{3,}") + r")", comparable)
    trailing_year_match = re.search(r"(?:" + (model or "[A-Z]{3,}") + r")\s*((?:19|20)\d{2})\b", comparable)
    year = int(year_match.group(1)) if year_match else int(trailing_year_match.group(1)) if trailing_year_match else None
    page = pages[0][0] if pages else None
    return {
        "brand": FieldValue(raw_value=brand, normalized_value=brand, confidence="medium" if brand else "low", source_page=page),
        "model": FieldValue(raw_value=model, normalized_value=model, confidence="medium" if model else "low", source_page=page),
        "year": FieldValue(raw_value=str(year) if year else None, normalized_value=year, confidence="medium" if year else "low", source_page=page),
        "vin": extract_vin(pages),
        "plates": extract_plates(pages),
    }


def classify_document(text: str) -> tuple[str, str]:
    comparable = _comparison_text(text)
    if re.search(r"\b(ORDEN DE TRABAJO|SERVICIO|CAMBIO DE ACEITE|MANTENIMIENTO)\b", comparable):
        return "comprobante_servicio", "high"
    if re.search(r"\b(UUID|CFDI|FOLIO FISCAL)\b", comparable):
        return "factura_cfdi", "high"
    if "HISTORIAL" in comparable and "SERVICIO" in comparable:
        return "historial_servicio", "medium"
    if re.search(r"\bORDEN\s+DE\s+REPARACION\b", comparable):
        return "orden_reparacion", "medium"
    if re.search(r"\b(LIBRO|REGISTRO)\b.*\bMANTENIMIENTO\b", comparable):
        return "registro_mantenimiento", "medium"
    return "desconocido", "low"


def extract_dealer(pages: list[tuple[int, str]]) -> FieldValue:
    for page_number, page_text in pages:
        for line in page_text.splitlines():
            comparable = _comparison_text(line)
            if any(token in comparable for token in ("S. DE R.L.", "S.A. DE C.V.", "DISTRIBUIDOR", "AGENCIA")):
                cleaned = re.sub(r"\s*\([^)]*$", "", line).strip(" ,")
                if cleaned:
                    return FieldValue(cleaned, cleaned, "medium", page_number)
    return FieldValue()


def extract_repair_order(pages: list[tuple[int, str]]) -> FieldValue:
    pattern = re.compile(
        r"\b[A-Z]{1,3}-[A-Z]?\d{5,}\b|\bO\.?T\.?\s*[:#-]?\s*(?=[A-Z0-9-]*\d)[A-Z0-9-]{4,}\b",
        re.IGNORECASE,
    )
    matches = [(page, match.group(0).upper()) for page, _, match in _iter_matches(pages, pattern)]
    return _choose_by_frequency(matches, lambda value: re.sub(r"\s+", "", value))


def _service_lines(pages: list[tuple[int, str]]) -> list[str]:
    lines: list[str] = []
    markers = ("SERVICIO", "MANTENIMIENTO", "CAMBIO DE ACEITE", "FILTRO DE ACEITE", "ROTACION")
    for _, text in pages:
        for line in text.splitlines():
            compact = " ".join(line.split())
            comparable = _comparison_text(compact)
            if any(marker in comparable for marker in markers) and "MANO DE OBRA" not in comparable:
                if any(marker in comparable for marker in ("COSTO", "SUBTOTAL", "TOTAL", "#PARTE")):
                    continue
                compact = re.sub(
                    r"^(?:#?OPERACION\s+\d+|OPERARIO:\s*\w+|(?=[A-Z0-9-]*\d)[A-Z0-9-]{4,})\s*",
                    "",
                    compact,
                    flags=re.IGNORECASE,
                )
                if compact and compact not in lines:
                    lines.append(compact)
    return lines[:5]


def classify_service(text: str) -> tuple[str, Optional[str], Optional[bool], bool, list[str]]:
    comparable = _comparison_text(text)
    positive = sum(
        marker in comparable
        for marker in ("MANTENIMIENTO", "CAMBIO DE ACEITE", "FILTRO DE ACEITE", "SERVICIO ", "REVISION PERIODICA")
    )
    negative_markers = ("CAMPANA", "GARANTIA", "REPARACION", "HOJALATERIA", "DIAGNOSTICO", "TORNO")
    negative = sum(marker in comparable for marker in negative_markers)
    if positive >= 2 or ("CAMBIO DE ACEITE" in comparable and "FILTRO" in comparable):
        return "preventive_maintenance", "Mantenimiento preventivo", True, False, []
    if "CAMPANA" in comparable:
        return "campaign", "Campaña", False, False, []
    if "GARANTIA" in comparable and positive == 0:
        return "warranty", "Garantía", False, False, []
    if negative:
        return "repair", "Reparación", False, False, []
    if "SERVICIO" in comparable:
        return "unknown", None, None, True, ["El servicio no contiene evidencia suficiente para confirmar mantenimiento programado."]
    return "unknown", None, None, True, ["No fue posible clasificar el evento de servicio."]


def parse_document(result: ExtractionResult) -> ParsedDocument:
    pages = [(page.page_number, page.text) for page in result.pages if page.text]
    text = result.text
    if not text:
        return ParsedDocument("desconocido", "low", {}, [], list(result.warnings))
    fields = extract_vehicle(pages)
    document_type, document_confidence = classify_document(text)
    service_date = extract_date(pages)
    mileage = extract_mileage(pages)
    dealer = extract_dealer(pages)
    repair_order = extract_repair_order(pages)
    lines = _service_lines(pages)
    description = " / ".join(lines) or None
    category, service_type, resets, review, classification_warnings = classify_service(description or text)
    warnings = list(result.warnings)
    if not service_date.normalized_value:
        warnings.append("No se detectó una fecha de servicio confiable.")
    if mileage.normalized_value is None:
        warnings.append("No se detectó un kilometraje confiable.")
    event_confidence = "high" if service_date.confidence == mileage.confidence == "high" and resets is True else "medium"
    event = ParsedServiceEvent(
        service_date=service_date,
        mileage=mileage,
        repair_order_number=repair_order,
        dealer=dealer,
        service_category=category,
        service_type=service_type,
        description=description,
        resets_maintenance_interval=resets,
        confidence=event_confidence,
        requires_human_review=review or not service_date.normalized_value or mileage.normalized_value is None,
        warnings=classification_warnings,
    )
    return ParsedDocument(document_type, document_confidence, fields, [event], warnings)
