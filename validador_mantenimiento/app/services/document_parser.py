from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Optional

from app.services.extraction_service import ExtractionResult, LayoutWord


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
    works: list[str]
    work_evidence: list[dict[str, Any]]
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
    document_generated_at: FieldValue = field(default_factory=FieldValue)
    layout_debug: dict[str, Any] = field(default_factory=dict)


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


def extract_history_vehicle(pages: list[tuple[int, str]]) -> dict[str, FieldValue]:
    """Un historial no incluye necesariamente las etiquetas de placa/año de una orden individual."""
    fields = extract_vehicle(pages)
    # En la tabla aparecen códigos de operación como HID313 y años de impresión. No son placa/año del vehículo.
    fields["plates"] = FieldValue()
    fields["year"] = FieldValue()
    return fields


def classify_document(text: str) -> tuple[str, str]:
    comparable = _comparison_text(text)
    if "HISTORIAL" in comparable and "SERVICIO" in comparable:
        return "historial_servicio", "high"
    if re.search(r"\b(ORDEN DE TRABAJO|SERVICIO|CAMBIO DE ACEITE|MANTENIMIENTO)\b", comparable):
        return "comprobante_servicio", "high"
    if re.search(r"\b(UUID|CFDI|FOLIO FISCAL)\b", comparable):
        return "factura_cfdi", "high"
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


def _semantic_text(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", _comparison_text(value)).strip()


def classify_service_event(
    *, works: list[str], description: Optional[str] = None, document_type: str = "desconocido"
) -> tuple[str, Optional[str], Optional[bool], bool, list[str]]:
    """Clasifica un evento completo; las obras se evalúan en conjunto, no aisladas."""
    normalized_works = [_semantic_text(work) for work in works if work]
    combined = " ".join([*normalized_works, _semantic_text(description or "")])
    strong_preventive = any(
        phrase in combined
        for phrase in (
            "CAMBIO ACEITE Y FILTRO",
            "CAMBIO DE ACEITE Y FILTRO",
            "CAMBIO ACEITE",
            "CAMBIO DE ACEITE",
            "FILTRO DE ACEITE",
            "SERVICIO DE MANTENIMIENTO",
            "MANTENIMIENTO PREVENTIVO",
        )
    )
    scheduled_service = bool(
        re.search(r"\bSERVICIO\s+(?:DE\s+)?\d+(?:\s|,)*(?:MIL|KM)\b", combined)
        or re.search(r"\bSERVICIO\s+\d{1,3}(?:\s\d{3}|000)\s*KM\b", combined)
    )
    complementary = any(
        phrase in combined
        for phrase in ("ROTACION", "FILTRO AIRE", "FILTRO COMBUSTIBLE", "BUJIA", "INSPECCION")
    )
    if strong_preventive or scheduled_service:
        return "preventive_maintenance", "Mantenimiento preventivo", True, False, []
    if "CAMPANA" in combined:
        return "campaign", "Campaña", False, False, []
    if "GARANTIA" in combined:
        return "warranty", "Garantía", False, False, []
    if any(
        phrase in combined
        for phrase in (
            "CANCELADA",
            "REPARACION",
            "REEMP MOLDURA",
            "REEMPLAZO MOLDURA",
            "TORNO DISCOS",
            "TORNO DE DISCOS",
            "SERVICIO FRENOS",
            "AIRE ACONDICIONADO",
            "DIAGNOSTICO",
            "HOJALATERIA",
        )
    ):
        return "repair", "Reparación", False, False, []
    if complementary:
        return "other", "Servicio complementario", False, False, []
    if "SERVICIO" in combined or document_type == "historial_servicio":
        return "unknown", None, None, True, ["No hay evidencia suficiente para confirmar mantenimiento preventivo."]
    return "unknown", None, None, True, ["No fue posible clasificar el evento de servicio."]


def classify_service(text: str) -> tuple[str, Optional[str], Optional[bool], bool, list[str]]:
    """Compatibilidad con el flujo de documentos individuales existente."""
    works = [segment.strip() for segment in text.split("/") if segment.strip()]
    return classify_service_event(works=works, description=text)


@dataclass(frozen=True)
class LayoutLine:
    page_number: int
    top: float
    bottom: float
    words: list[LayoutWord]


@dataclass(frozen=True)
class HistoryColumns:
    page_number: int
    header_top: float
    date_start: float
    date_end: float
    order_start: float
    order_end: float
    kilometer_start: float
    kilometer_end: float
    work_start: float


def _layout_lines(words: list[LayoutWord], tolerance: float = 3.0) -> list[LayoutLine]:
    lines: list[LayoutLine] = []
    for page_number in sorted({word.page_number for word in words}):
        page_words = sorted((word for word in words if word.page_number == page_number), key=lambda word: (word.top, word.x0))
        grouped: list[list[LayoutWord]] = []
        for word in page_words:
            if not grouped or abs(word.top - grouped[-1][0].top) > tolerance:
                grouped.append([word])
            else:
                grouped[-1].append(word)
        lines.extend(
            LayoutLine(
                page_number=page_number,
                top=min(word.top for word in line),
                bottom=max(word.bottom for word in line),
                words=sorted(line, key=lambda word: word.x0),
            )
            for line in grouped
        )
    return lines


def _find_history_columns(lines: list[LayoutLine]) -> dict[int, HistoryColumns]:
    columns: dict[int, HistoryColumns] = {}
    for line in lines:
        labels = {_semantic_text(word.text): word for word in line.words}
        if not {"FECHA", "ORDEN", "KMS", "MECANICO", "TRABAJO"}.issubset(labels):
            continue
        columns[line.page_number] = HistoryColumns(
            page_number=line.page_number,
            header_top=line.top,
            date_start=max(0, labels["FECHA"].x0 - 5),
            date_end=labels["ORDEN"].x0 - 2,
            order_start=labels["ORDEN"].x0 - 2,
            order_end=labels["ASESOR"].x0 - 2 if "ASESOR" in labels else labels["KMS"].x0 - 2,
            kilometer_start=labels["KMS"].x0 - 18,
            kilometer_end=labels["MECANICO"].x0 - 2,
            work_start=labels["TRABAJO"].x0 + 10,
        )
    return columns


def _words_text(words: list[LayoutWord]) -> str:
    return " ".join(word.text for word in sorted(words, key=lambda word: word.x0)).strip()


def _work_evidence(words: list[LayoutWord]) -> dict[str, Any]:
    return {
        "raw_text": _words_text(words),
        "source_page": words[0].page_number if words else None,
        "coordinates": {
            "x0": min((word.x0 for word in words), default=None),
            "x1": max((word.x1 for word in words), default=None),
            "top": min((word.top for word in words), default=None),
            "bottom": max((word.bottom for word in words), default=None),
        },
    }


def _history_generated_at(lines: list[LayoutLine], columns: dict[int, HistoryColumns]) -> FieldValue:
    for line in lines:
        page_columns = columns.get(line.page_number)
        if not page_columns or line.top >= page_columns.header_top:
            continue
        for word in line.words:
            if word.x0 <= page_columns.date_end and DATE_PATTERN.fullmatch(word.text):
                normalized = _normalize_date(word.text)
                if normalized:
                    return FieldValue(word.text, normalized, "medium", line.page_number)
    return FieldValue()


def _history_event_from_record(record: dict[str, Any]) -> ParsedServiceEvent:
    works = record["works"]
    description = " / ".join(works) or None
    classification_context = " ".join([*record["context"], *(works or [])])
    category, service_type, resets, review, warnings = classify_service_event(
        works=works,
        description=classification_context,
        document_type="historial_servicio",
    )
    service_date: FieldValue = record["service_date"]
    mileage: FieldValue = record["mileage"]
    if service_date.normalized_value is None:
        warnings = [*warnings, "Fila de historial sin fecha de servicio válida."]
    if mileage.normalized_value is None:
        warnings = [*warnings, "Fila de historial sin kilometraje en la columna Kms."]
    confidence = "high" if service_date.normalized_value and mileage.normalized_value is not None else "low"
    return ParsedServiceEvent(
        service_date=service_date,
        mileage=mileage,
        repair_order_number=record["repair_order_number"],
        dealer=FieldValue(),
        service_category=category,
        service_type=service_type,
        description=description,
        works=works,
        work_evidence=record["work_evidence"],
        resets_maintenance_interval=resets,
        confidence=confidence,
        requires_human_review=review or service_date.normalized_value is None or mileage.normalized_value is None,
        warnings=warnings,
    )


def parse_service_history(result: ExtractionResult, fields: dict[str, FieldValue]) -> ParsedDocument:
    lines = _layout_lines(result.words)
    columns = _find_history_columns(lines)
    if not columns:
        return ParsedDocument(
            "historial_servicio",
            "low",
            fields,
            [],
            ["No se encontraron las columnas Fecha, Orden, Kms, Mecánico y Trabajo en el historial."],
        )

    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        page_columns = columns.get(line.page_number)
        if not page_columns or line.top <= page_columns.header_top + 4:
            continue
        date_words = [
            word
            for word in line.words
            if page_columns.date_start <= word.x0 < page_columns.date_end and DATE_PATTERN.fullmatch(word.text)
        ]
        work_words = [word for word in line.words if word.x0 >= page_columns.work_start]
        if date_words:
            if current:
                records.append(current)
            date_word = date_words[0]
            kilometer_words = [
                word
                for word in line.words
                if page_columns.kilometer_start <= word.x0 < page_columns.kilometer_end
                and _normalize_mileage(word.text) is not None
                and not (1900 <= (_normalize_mileage(word.text) or 0) <= 2100)
            ]
            kilometer_word = next((word for word in kilometer_words if _normalize_mileage(word.text) is not None), None)
            order_words = [
                word for word in line.words if page_columns.order_start <= word.x0 < page_columns.order_end
            ]
            context_words = [
                word
                for word in line.words
                if page_columns.kilometer_end <= word.x0 < page_columns.work_start
            ]
            current = {
                "service_date": FieldValue(
                    date_word.text, _normalize_date(date_word.text), "high", line.page_number
                ),
                "mileage": FieldValue(
                    kilometer_word.text if kilometer_word else None,
                    _normalize_mileage(kilometer_word.text) if kilometer_word else None,
                    "high" if kilometer_word else "low",
                    line.page_number,
                ),
                "repair_order_number": FieldValue(
                    _words_text(order_words) or None,
                    _words_text(order_words) or None,
                    "medium" if order_words else "low",
                    line.page_number,
                ),
                "works": [],
                "work_evidence": [],
                "context": [_words_text(context_words)] if context_words else [],
            }
        if current and work_words:
            work = _work_evidence(work_words)
            if work["raw_text"]:
                current["works"].append(work["raw_text"])
                current["work_evidence"].append(work)
    if current:
        records.append(current)

    events = [_history_event_from_record(record) for record in records]
    generated_at = _history_generated_at(lines, columns)
    latest = max(
        (event for event in events if event.resets_maintenance_interval is True and event.service_date.normalized_value),
        key=lambda event: event.service_date.normalized_value,
        default=None,
    )
    layout_debug = {
        "strategy": "history_table",
        "headers": [
            {
                "page": column.page_number,
                "date_x": column.date_start,
                "kms_x": column.kilometer_start,
                "work_x": column.work_start,
            }
            for column in columns.values()
        ],
        "event_count": len(events),
        "latest_preventive_maintenance": {
            "date": latest.service_date.normalized_value.isoformat(),
            "mileage_km": latest.mileage.normalized_value,
            "reason": "Evento preventivo más reciente por fecha de servicio; se excluyen campaña, cancelación y reparación.",
        }
        if latest
        else None,
    }
    return ParsedDocument(
        "historial_servicio",
        "high" if events else "medium",
        fields,
        events,
        list(result.warnings),
        document_generated_at=generated_at,
        layout_debug=layout_debug,
    )


def parse_document(result: ExtractionResult) -> ParsedDocument:
    pages = [(page.page_number, page.text) for page in result.pages if page.text]
    text = result.text
    if not text:
        return ParsedDocument("desconocido", "low", {}, [], list(result.warnings))
    document_type, document_confidence = classify_document(text)
    fields = extract_history_vehicle(pages) if document_type == "historial_servicio" else extract_vehicle(pages)
    if document_type == "historial_servicio" and result.words:
        return parse_service_history(result, fields)
    service_date = extract_date(pages)
    mileage = extract_mileage(pages)
    dealer = extract_dealer(pages)
    repair_order = extract_repair_order(pages)
    lines = _service_lines(pages)
    description = " / ".join(lines) or None
    category, service_type, resets, review, classification_warnings = classify_service_event(
        works=lines,
        description=description or text,
        document_type=document_type,
    )
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
        works=lines,
        work_evidence=[{"raw_text": line, "source_page": None, "coordinates": None} for line in lines],
        resets_maintenance_interval=resets,
        confidence=event_confidence,
        requires_human_review=review or not service_date.normalized_value or mileage.normalized_value is None,
        warnings=classification_warnings,
    )
    return ParsedDocument(document_type, document_confidence, fields, [event], warnings)
