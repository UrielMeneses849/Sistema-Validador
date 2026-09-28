from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.core.config import EVIDENCE_CROP_DIR, TRAINING_DATA_DIR
from app.models.service_event import ServiceEvent


ANNOTATIONS_FILE = TRAINING_DATA_DIR / "annotations" / "verified.jsonl"


def resolve_evidence_crop(crop_id: str) -> Path:
    """Resuelve sólo rutas relativas que permanezcan dentro del almacén de recortes."""
    if not crop_id or Path(crop_id).is_absolute():
        raise ValueError("Identificador de recorte inválido.")
    root = EVIDENCE_CROP_DIR.resolve()
    candidate = (root / crop_id).resolve()
    if root not in candidate.parents:
        raise ValueError("Identificador de recorte fuera del almacén permitido.")
    return candidate


def _existing_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    identifiers: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("sample_id"):
            identifiers.add(str(payload["sample_id"]))
    return identifiers


def _ocr_prediction(evidence: dict[str, Any]) -> str | None:
    for candidate in evidence.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        if candidate.get("label") == "Corrección manual":
            continue
        value = candidate.get("rawText", candidate.get("raw_value"))
        if value is not None:
            return str(value)
    value = evidence.get("raw_value")
    return str(value) if value is not None else None


def _verified_text(value: date | int | str | None) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    return str(value) if value is not None else None


def record_human_verified_event(event: ServiceEvent) -> list[dict[str, Any]]:
    """Guarda sólo crops fecha/km de un evento confirmado; evita duplicados por contenido."""
    if not event.user_confirmed:
        return []
    ANNOTATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = _existing_ids(ANNOTATIONS_FILE)
    created: list[dict[str, Any]] = []
    fields = (
        ("date", "date", event.service_date),
        ("mileage_km", "mileage", event.mileage_km),
    )
    for evidence_key, field_type, verified in fields:
        verified_value = _verified_text(verified)
        evidence = (event.field_evidence or {}).get(evidence_key)
        if verified_value is None or not isinstance(evidence, dict) or not evidence.get("crop_id"):
            continue
        try:
            source = resolve_evidence_crop(str(evidence["crop_id"]))
        except ValueError:
            continue
        if not source.is_file():
            continue
        content_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        sample_id = hashlib.sha256(
            f"{content_hash}:{field_type}:{verified_value}".encode("utf-8")
        ).hexdigest()
        if sample_id in existing:
            continue
        suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
        destination = TRAINING_DATA_DIR / "crops" / ("dates" if field_type == "date" else "mileage") / f"{content_hash}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copyfile(source, destination)
        payload = {
            "sample_id": sample_id,
            "image": destination.relative_to(TRAINING_DATA_DIR).as_posix(),
            "field": field_type,
            "ocr_prediction": _ocr_prediction(evidence),
            "verified_value": verified_value,
            "text": verified_value,
            "source_document": event.document_id,
            "source_page": event.source_page or 1,
            "page_group": f"document:{event.document_id}:page:{event.source_page or 1}",
            "confidence": evidence.get("confidence_score", event.field_evidence.get("combined_confidence")),
            "preprocessing_variant": next(
                iter(
                    candidate.get("variants", [])
                    for candidate in evidence.get("candidates", [])
                    if isinstance(candidate, dict) and candidate.get("selected")
                ),
                [],
            ),
            "human_confirmed": True,
            "created_at": datetime.now(UTC).isoformat(),
        }
        with ANNOTATIONS_FILE.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        existing.add(sample_id)
        created.append(payload)
    return created
