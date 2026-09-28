from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Optional

from app.core.config import OCR_REVIEW_THRESHOLD
from app.services.extraction_service import ExtractionResult, LayoutWord


DATE_PATTERN = re.compile(r"\b(0?[1-9]|[12]\d|3[01])[\/\-.](0?[1-9]|1[0-2])[\/\-.]((?:19|20)?\d{2})\b")
ISO_DATE_PATTERN = re.compile(
    r"\b((?:19|20)\d{2})-(0[1-9]|1[0-2])-([0-2]\d|3[01])"
    r"(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b",
    re.IGNORECASE,
)
SPANISH_MONTHS = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6,
    "JULIO": 7, "AGOSTO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10,
    "NOVIEMBRE": 11, "DICIEMBRE": 12,
}
TEXTUAL_DATE_PATTERN = re.compile(
    r"\b(0?[1-9]|[12]\d|3[01])(?:\s+DE\s+|\s+|[\/\-.])(" + "|".join(SPANISH_MONTHS) + r")(?:\s+DE\s+|\s+|[\/\-.])((?:19|20)?\d{2})\b",
    re.IGNORECASE,
)
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
    confidence_score: float = 0.0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: bool = False
    evidence_metadata: dict[str, Any] = field(default_factory=dict)

    def evidence(self, method: str) -> dict[str, Any]:
        normalized = self.normalized_value.isoformat() if isinstance(self.normalized_value, date) else self.normalized_value
        return {
            "raw_value": self.raw_value,
            "normalized_value": normalized,
            "source_page": self.source_page,
            "extraction_method": method,
            "confidence": self.confidence,
            "confidence_score": round(self.confidence_score, 4),
            "candidates": self.candidates,
            "ambiguous": self.ambiguous,
            **self.evidence_metadata,
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
    confidence_score: float = 0.0
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


def _confidence_label(score: float) -> str:
    if score >= OCR_REVIEW_THRESHOLD:
        return "high"
    if score >= 0.60:
        return "medium"
    return "low"


def _token_confidence(words: list[LayoutWord], page_number: int, raw_value: str) -> float | None:
    raw_tokens = {token for token in re.findall(r"[A-Z0-9]+", _comparison_text(raw_value)) if token}
    matches = []
    for word in words:
        if word.page_number != page_number or word.confidence is None:
            continue
        word_tokens = set(re.findall(r"[A-Z0-9]+", _comparison_text(word.text)))
        if raw_tokens & word_tokens:
            matches.append(word.confidence)
    return sum(matches) / len(matches) if matches else None


def _combined_pattern_token_confidence(pattern_confidence: float, token_confidence: float | None) -> float:
    if token_confidence is None:
        return pattern_confidence
    return round(pattern_confidence * 0.55 + token_confidence * 0.45, 4)


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
    confidence = _confidence(occurrences * 2, occurrences)
    confidence_score = {"high": 0.94, "medium": 0.74, "low": 0.45}[confidence]
    candidates = [
        {"raw_value": item_raw, "normalized_value": item_value, "source_page": item_page}
        for item_page, item_raw, item_value in normalized if item_value is not None
    ]
    return FieldValue(raw, selected, confidence, page, confidence_score, candidates, len(values) > 1)


def _normalize_date(raw: str) -> Optional[date]:
    clean = raw.strip()
    iso_match = ISO_DATE_PATTERN.fullmatch(clean)
    if iso_match:
        year, month, day = iso_match.groups()
        normalized_year = int(year)
        month_number = int(month)
    else:
        match = DATE_PATTERN.fullmatch(clean)
        if match:
            day, month, year = match.groups()
            month_number = int(month)
        else:
            textual_match = TEXTUAL_DATE_PATTERN.fullmatch(_comparison_text(clean))
            if not textual_match:
                return None
            day, month_name, year = textual_match.groups()
            month_number = SPANISH_MONTHS[month_name.upper()]
        if len(year) == 2:
            numeric_year = int(year)
            normalized_year = (
                2000 + numeric_year if numeric_year <= 49 else 1900 + numeric_year
            )
            if normalized_year > date.today().year + 1 or normalized_year < 1950:
                return None
        else:
            normalized_year = int(year)
    try:
        return datetime(normalized_year, month_number, int(day)).date()
    except ValueError:
        return None


def extract_date(pages: list[tuple[int, str]], words: list[LayoutWord] | None = None) -> FieldValue:
    candidates: list[dict[str, Any]] = []
    for pattern, base_pattern_confidence in (
        (ISO_DATE_PATTERN, 0.97),
        (DATE_PATTERN, 0.96),
        (TEXTUAL_DATE_PATTERN, 0.93),
    ):
        for page, _, match in _iter_matches(pages, pattern):
            raw = match.group(0)
            normalized = _normalize_date(raw)
            if normalized is None:
                continue
            pattern_confidence = base_pattern_confidence
            if pattern is not ISO_DATE_PATTERN and len(match.groups()[-1]) == 2:
                pattern_confidence = min(pattern_confidence, 0.82)
            score = _combined_pattern_token_confidence(
                pattern_confidence, _token_confidence(words or [], page, raw)
            )
            candidates.append({
                "raw_value": raw,
                "normalized_value": normalized.isoformat(),
                "source_page": page,
                "confidence_score": score,
            })
    if not candidates:
        return FieldValue()
    candidates.sort(key=lambda item: item["confidence_score"], reverse=True)
    selected = candidates[0]
    distinct_values = {item["normalized_value"] for item in candidates}
    competing = [item for item in candidates[1:] if item["normalized_value"] != selected["normalized_value"] and abs(item["confidence_score"] - selected["confidence_score"]) <= 0.05]
    ambiguous = bool(competing)
    score = min(selected["confidence_score"], 0.75) if ambiguous else selected["confidence_score"]
    return FieldValue(selected["raw_value"], date.fromisoformat(selected["normalized_value"]), _confidence_label(score), selected["source_page"], score, candidates, ambiguous or len(distinct_values) > 1)


def _normalize_mileage(raw: str) -> Optional[int]:
    digits = re.sub(r"[^0-9]", "", raw)
    if not digits:
        return None
    value = int(digits)
    return value if 0 <= value <= 2_000_000 else None


def extract_mileage(pages: list[tuple[int, str]], words: list[LayoutWord] | None = None) -> FieldValue:
    candidates: list[dict[str, Any]] = []
    scores: Counter[int] = Counter()
    for page_number, page_text, match in _iter_matches(pages, MILEAGE_PATTERN):
        raw = match.group(0)
        value = _normalize_mileage(raw)
        if value is None or 1900 <= value <= 2100:
            continue  # Años no son odómetros.
        start, end = match.span()
        context = _comparison_text(page_text[max(0, start - 70) : min(len(page_text), end + 70)])
        before = _comparison_text(page_text[max(0, start - 55) : start])
        after = _comparison_text(page_text[end : min(len(page_text), end + 20)])
        scheduled_interval = bool(
            re.search(r"\b(?:SERVICIO|MANTENIMIENTO)\b.{0,45}$", before)
            and re.match(r"\s*KMS?\.?\b", after)
        )
        if scheduled_interval:
            continue  # "Servicio de 6,000 Kms." es un intervalo comercial, no el odómetro real.
        has_labeled_context = bool(re.search(r"\b(KILOMETRAJE|KILOMETROS?|ODOMETRO)\b", context))
        has_km_context = bool(re.search(r"\bKMS?\b", context))
        has_explicit_km_label = bool(
            re.search(r"\bKMS?\.?\s*:?\s*$", before)
            or re.match(r"\s*KMS?\.?\b", after)
        )
        if not has_labeled_context and not has_km_context:
            continue  # Un número sin evidencia semántica no se trata como odómetro.
        score = 1
        if raw.lstrip().startswith("0") and len(re.sub(r"\D", "", raw)) >= 6:
            score -= 6  # Códigos de operación/refacción suelen tener ceros iniciales.
        if has_labeled_context or has_explicit_km_label:
            score += 8
        if DATE_PATTERN.search(page_text[end : min(len(page_text), end + 30)]):
            score += 4
        if VIN_PATTERN.search(page_text[end : min(len(page_text), end + 100)]):
            score += 4
        if re.search(r"\b(SERVICIO|OPERACION|PARTE|COSTO|TOTAL|IVA|SUBTOTAL)\b", context):
            score -= 4
        pattern_confidence = 0.97 if has_labeled_context or has_explicit_km_label else 0.84
        confidence_score = _combined_pattern_token_confidence(
            pattern_confidence, _token_confidence(words or [], page_number, raw)
        )
        candidates.append({
            "source_page": page_number,
            "raw_value": raw,
            "normalized_value": value,
            "ranking_score": score,
            "confidence_score": confidence_score,
        })
        scores[value] += score
    if not candidates:
        return FieldValue()
    selected = max(
        scores,
        key=lambda value: (
            scores[value],
            sum(item["normalized_value"] == value for item in candidates),
            -next(index for index, item in enumerate(candidates) if item["normalized_value"] == value),
        ),
    )
    selected_candidate = max(
        (item for item in candidates if item["normalized_value"] == selected),
        key=lambda item: item["confidence_score"],
    )
    competing = [
        item for item in candidates
        if item["normalized_value"] != selected
        and scores[item["normalized_value"]] >= scores[selected] - 1
        and abs(item["confidence_score"] - selected_candidate["confidence_score"]) <= 0.05
    ]
    ambiguous = bool(competing)
    confidence_score = min(selected_candidate["confidence_score"], 0.75) if ambiguous else selected_candidate["confidence_score"]
    public_candidates = [
        {
            "raw_value": item["raw_value"],
            "normalized_value": item["normalized_value"],
            "source_page": item["source_page"],
            "confidence_score": round(item["confidence_score"], 4),
        }
        for item in candidates
    ]
    return FieldValue(
        selected_candidate["raw_value"], selected, _confidence_label(confidence_score),
        selected_candidate["source_page"], confidence_score, public_candidates, ambiguous,
    )


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
        # La normalización convierte "12,000" en "12 000". Se aceptan
        # ambas formas y la variante "Servicio de mantenimiento de ...".
        re.search(
            r"\bSERVICIO(?:\s+DE)?(?:\s+MANTENIMIENTO)?(?:\s+DE)?\s+"
            r"\d{1,3}(?:\s+\d{3}|000)?\s+(?:MIL|KMS?)\b",
            combined,
        )
        # Algunos distribuidores facturan los paquetes programados como
        # S24SILVERADO, S36SILVERADO, etc. El número representa el intervalo
        # en miles de kilómetros y el sufijo identifica el modelo.
        or any(
            6 <= int(interval) <= 300 and int(interval) % 6 == 0
            for interval, _model in re.findall(
                r"\bS(\d{1,3})([A-Z]{3,})(?=\s|$)", combined
            )
        )
        # Las órdenes suelen describir el paquete con ordinales en vez de
        # repetir el kilometraje: "Primer mantenimiento" o
        # "Quinto servicio programado".
        or re.search(
            r"\b(?:PRIMER(?:O)?|SEGUNDO|TERCER(?:O)?|CUARTO|QUINTO|SEXTO|"
            r"SEPTIMO|OCTAVO|NOVENO|DECIMO)\s+"
            r"(?:SERVICIO|MANTENIMIENTO)(?:\s+(?:PROGRAMADO|DE\s+CORTESIA))?\b",
            combined,
        )
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


def _bounding_box(word: LayoutWord) -> dict[str, float]:
    return {
        "x0": round(word.x0, 2),
        "x1": round(word.x1, 2),
        "top": round(word.top, 2),
        "bottom": round(word.bottom, 2),
    }


def _spatial_candidate(
    *, value: Any, raw: str, label: str, word: LayoutWord, score: float, reasons: list[str]
) -> dict[str, Any]:
    return {
        "value": value.isoformat() if isinstance(value, date) else value,
        "rawText": raw,
        "label": label,
        "page": word.page_number,
        "boundingBox": _bounding_box(word),
        "score": round(max(0.0, min(1.0, score)), 4),
        "reasons": reasons,
        "selected": False,
    }


def _field_from_spatial_candidates(
    candidates: list[dict[str, Any]], *, is_date: bool = False, force_ambiguous: bool = False
) -> FieldValue:
    if not candidates:
        return FieldValue()
    ranked = sorted(candidates, key=lambda candidate: candidate["score"], reverse=True)
    selected_value = ranked[0]["value"]
    value_counts = Counter(candidate["value"] for candidate in ranked)
    selected_occurrences = value_counts[selected_value]
    ambiguous = force_ambiguous or any(
        candidate["value"] != selected_value
        and candidate["score"] >= ranked[0]["score"] - 0.08
        and (
            not is_date
            or value_counts[candidate["value"]] >= selected_occurrences
        )
        for candidate in ranked[1:]
    )
    selected_score = min(ranked[0]["score"], 0.75) if ambiguous else ranked[0]["score"]
    public_candidates = [
        {**candidate, "selected": candidate["value"] == selected_value}
        for candidate in ranked
    ]
    normalized: Any = date.fromisoformat(selected_value) if is_date else selected_value
    return FieldValue(
        raw_value=ranked[0]["rawText"],
        normalized_value=normalized,
        confidence=_confidence_label(selected_score),
        source_page=ranked[0]["page"],
        confidence_score=selected_score,
        candidates=public_candidates,
        ambiguous=ambiguous,
    )


def extract_spatial_date(words: list[LayoutWord]) -> FieldValue:
    """Prioriza fechas unidas a etiquetas por geometría, no por orden del texto plano."""
    lines = _layout_lines(words)
    candidates: list[dict[str, Any]] = []
    for line in lines:
        for label_word in line.words:
            label = _semantic_text(label_word.text)
            if label == "FECHA":
                values = [
                    word for word in line.words
                    if word.x0 >= label_word.x1
                    and word.x0 - label_word.x1 <= 180
                    and _normalize_date(word.text) is not None
                ]
                if values:
                    value_word = min(values, key=lambda word: word.x0)
                    normalized = _normalize_date(value_word.text)
                    assert normalized is not None
                    qualifier = " ".join(
                        _semantic_text(word.text)
                        for word in line.words
                        if label_word.x1 <= word.x0 < value_word.x0
                    )
                    if "SERVICIO" in qualifier:
                        base_score = 0.995
                        reason = "Fecha identificada explícitamente como fecha de servicio."
                    elif any(
                        token in qualifier
                        for token in ("EMISION", "CERTIFICACION", "TIMBRADO", "VENCIMIENTO")
                    ):
                        base_score = 0.82
                        reason = "Fecha documental secundaria; no sustituye la fecha de servicio."
                    else:
                        base_score = 0.93
                        reason = "Fecha situada a la derecha de la etiqueta Fecha."
                    candidates.append(_spatial_candidate(
                        value=normalized,
                        raw=value_word.text,
                        label="Fecha",
                        word=value_word,
                        score=_combined_pattern_token_confidence(base_score, value_word.confidence),
                        reasons=[reason],
                    ))
            if label in {"REPAR", "REPARACION"} or label.endswith("REPAR"):
                below = [
                    word for candidate_line in lines
                    if candidate_line.page_number == line.page_number
                    and 0 <= candidate_line.top - line.bottom <= 45
                    for word in candidate_line.words
                    if _normalize_date(word.text) is not None
                    and label_word.x0 - 20 <= (word.x0 + word.x1) / 2 <= label_word.x1 + 20
                ]
                if below:
                    value_word = min(below, key=lambda word: word.top)
                    normalized = _normalize_date(value_word.text)
                    assert normalized is not None
                    candidates.append(_spatial_candidate(
                        value=normalized,
                        raw=value_word.text,
                        label="F. Repar.",
                        word=value_word,
                        score=_combined_pattern_token_confidence(0.97, value_word.confidence),
                        reasons=["Fecha situada debajo del encabezado F. Repar."],
                    ))
    counts = Counter(candidate["value"] for candidate in candidates)
    for candidate in candidates:
        if counts[candidate["value"]] > 1:
            candidate["score"] = round(min(1.0, candidate["score"] + 0.01), 4)
            candidate["reasons"] = [*candidate["reasons"], "La misma fecha aparece en otra referencia de servicio."]
    return _field_from_spatial_candidates(candidates, is_date=True)


def _integer_word_value(word: LayoutWord) -> int | None:
    raw = word.text.strip()
    if re.fullmatch(r"\d{1,3}(?:[,\s]\d{3})+|\d{4,7}", raw) is None:
        return None
    value = _normalize_mileage(raw)
    if value is None or 1900 <= value <= 2100:
        return None
    return value


def extract_spatial_mileage(words: list[LayoutWord]) -> FieldValue:
    """Selecciona el odómetro por etiqueta, columna y distancia vertical."""
    lines = _layout_lines(words)
    candidates: list[dict[str, Any]] = []
    for line in lines:
        for label_word in line.words:
            label = _semantic_text(label_word.text)
            if label not in {"KM", "KMS", "KM ENT", "KM SAL", "KILOMETRAJE", "ODOMETRO", "ODOMETER"}:
                continue
            if label in {"KM", "KMS"}:
                label_index = line.words.index(label_word)
                previous_word = line.words[label_index - 1] if label_index else None
                # En "9,842 km 10,000 km", ambos "km" son unidades que
                # siguen a una cifra, no etiquetas del odómetro. Sólo se
                # acepta KM/KMS como etiqueta cuando precede al valor, como
                # en "Kms. 46145" o "KM: 105849".
                if (
                    previous_word is not None
                    and _integer_word_value(previous_word) is not None
                    and 0 <= label_word.x0 - previous_word.x1 <= 25
                ):
                    continue
            same_line = [
                word for word in line.words
                if word.x0 >= label_word.x1
                and word.x0 - label_word.x1 <= 170
                and _integer_word_value(word) is not None
            ]
            below = [
                word for candidate_line in lines
                if candidate_line.page_number == line.page_number
                and 0 <= candidate_line.top - line.bottom <= 55
                for word in candidate_line.words
                if _integer_word_value(word) is not None
                and label_word.x0 - 15 <= (word.x0 + word.x1) / 2 <= label_word.x1 + 15
            ]
            pool = same_line if same_line else below
            if not pool:
                continue
            value_word = min(pool, key=lambda word: (abs(word.top - label_word.bottom), abs(word.x0 - label_word.x1)))
            value = _integer_word_value(value_word)
            assert value is not None
            in_column = value_word in below and value_word not in same_line
            base_score = 0.96 if label in {"KM ENT", "KM SAL"} and in_column else 0.94
            candidates.append(_spatial_candidate(
                value=value,
                raw=value_word.text,
                label={"KM ENT": "Km.Ent.", "KM SAL": "Km.Sal."}.get(label, label_word.text),
                word=value_word,
                score=_combined_pattern_token_confidence(base_score, value_word.confidence),
                reasons=[
                    "Valor situado debajo del encabezado y dentro de la misma columna."
                    if in_column else
                    "Valor situado junto a una etiqueta explícita de odómetro."
                ],
            ))

    counts = Counter(candidate["value"] for candidate in candidates)
    for candidate in candidates:
        if counts[candidate["value"]] > 1:
            candidate["score"] = round(min(1.0, candidate["score"] + 0.03), 4)
            candidate["reasons"] = [
                *candidate["reasons"],
                "Km.Ent. y Km.Sal. coinciden; se incrementa la confianza.",
            ]
    distinct_strong = {
        candidate["value"] for candidate in candidates
        if candidate["score"] >= 0.90
    }
    force_ambiguous = len(distinct_strong) > 1
    if force_ambiguous:
        # Sin una regla de negocio adicional, Km.Sal. es sólo una selección tentativa.
        for candidate in candidates:
            if candidate["label"] == "Km.Sal.":
                candidate["score"] = max(item["score"] for item in candidates) + 0.001
                candidate["reasons"] = [
                    *candidate["reasons"],
                    "Km.Ent. y Km.Sal. difieren; Km.Sal. es tentativo y requiere revisión.",
                ]
    return _field_from_spatial_candidates(candidates, force_ambiguous=force_ambiguous)


def _concept_field(
    raw: str | None, normalized: Any, *, page: int | None = None, score: float = 0.94,
    label: str | None = None, word: LayoutWord | None = None, reason: str = ""
) -> FieldValue:
    if raw is None:
        return FieldValue()
    candidates = []
    if word is not None:
        candidates.append(_spatial_candidate(
            value=normalized, raw=raw, label=label or "", word=word, score=score,
            reasons=[reason] if reason else [],
        ) | {"selected": True})
    return FieldValue(raw, normalized, _confidence_label(score), page, score, candidates)


def extract_document_concepts(
    pages: list[tuple[int, str]], words: list[LayoutWord], actual_mileage: FieldValue,
    repair_order: FieldValue,
) -> dict[str, FieldValue]:
    """Mantiene separados odómetro, intervalo, códigos, partes, importes y orden."""
    text = "\n".join(page_text for _, page_text in pages)
    comparable = _comparison_text(text)
    interval_match = re.search(
        r"\bSERVICIO\s+(?:DE\s+)?(\d{1,3})\s*(MIL|,?000)?\s*KM\b", comparable
    )
    interval = FieldValue()
    if interval_match:
        number = int(interval_match.group(1))
        interval_value = number * 1000 if interval_match.group(2) in {"MIL", "000", ",000"} else number
        interval = _concept_field(
            interval_match.group(0), interval_value, page=pages[0][0] if pages else None,
            score=0.96, label="Servicio programado",
        )

    lines = _layout_lines(words)
    operation_headers = [
        word for word in words if _semantic_text(word.text) in {"OPERACION", "N OPERACION"}
    ]
    operation_candidates = [
        word for word in words
        if re.fullmatch(r"0\d{5,9}", word.text.strip())
        and any(
            header.page_number == word.page_number
            and -5 <= word.top - header.bottom <= 65
            for header in operation_headers
        )
    ]
    operation_word = min(operation_candidates, key=lambda word: word.top, default=None)
    operation = _concept_field(
        operation_word.text if operation_word else None,
        operation_word.text if operation_word else None,
        page=operation_word.page_number if operation_word else None,
        score=0.97,
        label="#Operación",
        word=operation_word,
        reason="Código con cero inicial dentro de la sección #Operación; se excluye del odómetro.",
    )

    part_values: list[str] = []
    amount_values: list[float] = []
    for line in lines:
        semantic_words = [_semantic_text(word.text) for word in line.words]
        if "PARTE" in semantic_words:
            header_index = semantic_words.index("PARTE")
            for word in line.words[header_index + 1:]:
                if _semantic_text(word.text) == "COSTO":
                    break
                if re.search(r"\d", word.text) and word.text not in part_values:
                    part_values.append(word.text)
                    break
        has_amount_label = any(label in semantic_words for label in ("COSTO", "TOTAL", "SUB TOTAL"))
        if has_amount_label:
            for word in line.words:
                if re.fullmatch(r"\$?\d{1,7}[.,]\d{2}", word.text.strip()):
                    amount = float(word.text.replace("$", "").replace(",", ""))
                    if amount not in amount_values:
                        amount_values.append(amount)

    return {
        "actualMileage": actual_mileage,
        "serviceIntervalKm": interval,
        "operationCode": operation,
        "partNumber": _concept_field(", ".join(part_values) or None, part_values or None, score=0.90),
        "monetaryAmount": _concept_field(
            ", ".join(f"{value:.2f}" for value in amount_values) or None,
            amount_values or None,
            score=0.95,
        ),
        "invoiceOrOrderNumber": repair_order,
    }


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
                    token_score = word.confidence if word.confidence is not None else 0.78
                    return FieldValue(word.text, normalized, _confidence_label(token_score), line.page_number, token_score)
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
    confidence_score = min(service_date.confidence_score, mileage.confidence_score)
    confidence = _confidence_label(confidence_score)
    field_review = (
        service_date.normalized_value is None
        or mileage.normalized_value is None
        or service_date.ambiguous
        or mileage.ambiguous
        or confidence_score < OCR_REVIEW_THRESHOLD
    )
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
        requires_human_review=review or field_review,
        confidence_score=confidence_score,
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
                    date_word.text, _normalize_date(date_word.text),
                    _confidence_label(date_word.confidence if date_word.confidence is not None else 0.96),
                    line.page_number, date_word.confidence if date_word.confidence is not None else 0.96,
                ),
                "mileage": FieldValue(
                    kilometer_word.text if kilometer_word else None,
                    _normalize_mileage(kilometer_word.text) if kilometer_word else None,
                    _confidence_label(kilometer_word.confidence if kilometer_word and kilometer_word.confidence is not None else 0.96 if kilometer_word else 0.0),
                    line.page_number,
                    kilometer_word.confidence if kilometer_word and kilometer_word.confidence is not None else 0.96 if kilometer_word else 0.0,
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


def _parse_specialized_image(result: ExtractionResult) -> ParsedDocument:
    """Convierte resultados fecha+km en el contrato neutral que consume VAL-002."""
    payloads = result.metadata.get("maintenance_image_events", [])
    events: list[ParsedServiceEvent] = []
    for payload in payloads:
        date_payload = payload.get("service_date") or {}
        mileage_payload = payload.get("mileage") or {}
        normalized_date = date_payload.get("normalized_value")
        try:
            parsed_date = date.fromisoformat(normalized_date) if normalized_date else None
        except (TypeError, ValueError):
            parsed_date = None
        normalized_mileage = mileage_payload.get("normalized_value")
        try:
            parsed_mileage = int(normalized_mileage) if normalized_mileage is not None else None
        except (TypeError, ValueError):
            parsed_mileage = None

        def candidates(field_payload: dict[str, Any], label: str) -> list[dict[str, Any]]:
            return [
                {
                    **candidate,
                    "value": candidate.get("normalized_value"),
                    "rawText": candidate.get("raw_value"),
                    "label": label,
                    "page": 1,
                    "boundingBox": payload.get("region"),
                    "score": candidate.get("confidence_score", 0.0),
                    "reasons": ["Consenso entre variantes preprocesadas del recorte especializado."],
                }
                for candidate in field_payload.get("candidates", [])
            ]

        service_date = FieldValue(
            raw_value=date_payload.get("raw_value"),
            normalized_value=parsed_date,
            confidence=date_payload.get("confidence", "low"),
            source_page=1,
            confidence_score=float(date_payload.get("confidence_score", 0.0)),
            candidates=candidates(date_payload, "Fecha"),
            ambiguous=bool(date_payload.get("ambiguous")),
            evidence_metadata={
                "crop_id": date_payload.get("crop_id"),
                "field_type": "date",
                "region": payload.get("region"),
            },
        )
        mileage = FieldValue(
            raw_value=mileage_payload.get("raw_value"),
            normalized_value=parsed_mileage,
            confidence=mileage_payload.get("confidence", "low"),
            source_page=1,
            confidence_score=float(mileage_payload.get("confidence_score", 0.0)),
            candidates=candidates(mileage_payload, "Kilometraje"),
            ambiguous=bool(mileage_payload.get("ambiguous")),
            evidence_metadata={
                "crop_id": mileage_payload.get("crop_id"),
                "field_type": "mileage",
                "region": payload.get("region"),
            },
        )
        pair_score = min(service_date.confidence_score, mileage.confidence_score)
        warnings = list(payload.get("warnings") or [])
        if service_date.normalized_value is None and not any("fecha" in item.lower() for item in warnings):
            warnings.append("No se detectó una fecha de servicio confiable.")
        if mileage.normalized_value is None and not any("kilometraje" in item.lower() for item in warnings):
            warnings.append("No se detectó un kilometraje confiable.")
        events.append(ParsedServiceEvent(
            service_date=service_date,
            mileage=mileage,
            repair_order_number=FieldValue(),
            dealer=FieldValue(),
            service_category="preventive_maintenance",
            service_type="Mantenimiento preventivo",
            description="Registro de mantenimiento detectado en imagen.",
            works=["Registro de mantenimiento"],
            work_evidence=[{"source": "service_box", "region": payload.get("region")}],
            resets_maintenance_interval=True,
            confidence=_confidence_label(pair_score),
            requires_human_review=bool(payload.get("requires_human_review", True)),
            confidence_score=pair_score,
            warnings=warnings,
        ))

    pages = [(page.page_number, page.text) for page in result.pages if page.text]
    fields = extract_vehicle(pages) if pages else {}
    review_count = sum(event.requires_human_review for event in events)
    return ParsedDocument(
        document_type="registro_mantenimiento",
        confidence="high" if events and not review_count else "medium" if events else "low",
        fields=fields,
        service_events=events,
        warnings=list(result.warnings),
        layout_debug={
            "strategy": "specialized_service_boxes",
            "event_count": len(events),
            "review_count": review_count,
            "external_fallback_used": False,
        },
    )


def parse_document(result: ExtractionResult) -> ParsedDocument:
    if isinstance(result.metadata.get("maintenance_image_events"), list):
        return _parse_specialized_image(result)
    pages = [(page.page_number, page.text) for page in result.pages if page.text]
    text = result.text
    if not text:
        return ParsedDocument("desconocido", "low", {}, [], list(result.warnings))
    document_type, document_confidence = classify_document(text)
    fields = extract_history_vehicle(pages) if document_type == "historial_servicio" else extract_vehicle(pages)
    if document_type == "historial_servicio" and result.words:
        return parse_service_history(result, fields)
    service_date = extract_spatial_date(result.words) if result.words else FieldValue()
    if service_date.normalized_value is None:
        service_date = extract_date(pages, result.words)
    mileage = extract_spatial_mileage(result.words) if result.words else FieldValue()
    if mileage.normalized_value is None:
        mileage = extract_mileage(pages, result.words)
    dealer = extract_dealer(pages)
    repair_order = extract_repair_order(pages)
    fields.update(extract_document_concepts(pages, result.words, mileage, repair_order))
    lines = _service_lines(pages)
    description = " / ".join(lines) or None
    category, service_type, resets, review, classification_warnings = classify_service_event(
        works=lines,
        # `lines` se limita para presentar un resumen legible. La
        # clasificación sí debe considerar el documento completo, pues la
        # descripción del paquete puede aparecer después de varios encabezados.
        description=text,
        document_type=document_type,
    )
    warnings = list(result.warnings)
    if not service_date.normalized_value:
        warnings.append("No se detectó una fecha de servicio confiable.")
    elif service_date.ambiguous:
        warnings.append("Se detectaron varias fechas posibles; confirma la fecha de servicio.")
    if mileage.normalized_value is None:
        warnings.append("No se detectó un kilometraje confiable.")
    elif mileage.ambiguous:
        warnings.append("Se detectaron varios kilometrajes posibles; confirma la lectura correcta.")
    event_confidence_score = min(service_date.confidence_score, mileage.confidence_score)
    event_confidence = _confidence_label(event_confidence_score)
    field_review = (
        event_confidence_score < OCR_REVIEW_THRESHOLD
        or service_date.ambiguous
        or mileage.ambiguous
    )
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
        requires_human_review=review or field_review or not service_date.normalized_value or mileage.normalized_value is None,
        confidence_score=event_confidence_score,
        warnings=classification_warnings,
    )
    return ParsedDocument(document_type, document_confidence, fields, [event], warnings)
