from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_GROUND_TRUTH = PROJECT_DIR / "benchmark_data" / "real_photos" / "ground_truth.jsonl"
GROUND_TRUTH_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class GroundTruthEvent:
    service_date: str
    mileage_km: int
    region_index: int | None = None


def normalize_ground_truth_event(
    service_date: str,
    mileage_km: str | int,
    region_index: str | int | None = None,
) -> GroundTruthEvent:
    try:
        normalized_date = date.fromisoformat(str(service_date).strip()).isoformat()
    except ValueError as exc:
        raise ValueError("service_date debe usar el formato YYYY-MM-DD y ser una fecha válida.") from exc

    raw_mileage = str(mileage_km).strip().replace(",", "").replace(".", "").replace(" ", "")
    if not re.fullmatch(r"\d+", raw_mileage):
        raise ValueError("mileage_km debe ser un entero no negativo.")
    normalized_region: int | None = None
    if region_index is not None:
        try:
            normalized_region = int(region_index)
        except (TypeError, ValueError) as exc:
            raise ValueError("region_index debe ser un entero positivo.") from exc
        if normalized_region < 1:
            raise ValueError("region_index debe ser un entero positivo.")
    return GroundTruthEvent(normalized_date, int(raw_mileage), normalized_region)


def _ground_truth_payload(event: GroundTruthEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "service_date": event.service_date,
        "mileage_km": event.mileage_km,
    }
    if event.region_index is not None:
        payload["region_index"] = event.region_index
    return payload


def image_sha256(image_path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(image_path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON inválido en {source}, línea {line_number}: {exc.msg}.") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Cada línea de {source} debe ser un objeto JSON (línea {line_number}).")
        rows.append(row)
    return rows


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(destination)


def save_ground_truth(
    image_path: str | Path,
    events: Iterable[GroundTruthEvent | dict[str, Any] | tuple[str, str | int]],
    *,
    ground_truth_path: str | Path = DEFAULT_GROUND_TRUTH,
    group: str = "development",
) -> tuple[dict[str, Any], bool]:
    image = Path(image_path).expanduser().resolve()
    if not image.is_file():
        raise ValueError(f"No existe la imagen: {image}")

    normalized_events: list[GroundTruthEvent] = []
    for event in events:
        if isinstance(event, GroundTruthEvent):
            normalized_events.append(
                normalize_ground_truth_event(event.service_date, event.mileage_km, event.region_index)
            )
        elif isinstance(event, dict):
            normalized_events.append(
                normalize_ground_truth_event(
                    str(event.get("service_date", "")),
                    event.get("mileage_km", ""),
                    event.get("region_index"),
                )
            )
        else:
            normalized_events.append(
                normalize_ground_truth_event(
                    event[0], event[1], event[2] if len(event) > 2 else None
                )
            )
    if not normalized_events:
        raise ValueError("Registra al menos un evento para la fotografía.")
    normalized_group = re.sub(r"[^a-z0-9_-]+", "-", group.strip().lower()).strip("-")
    if not normalized_group:
        raise ValueError("group debe identificar el conjunto completo de la fotografía.")

    content_hash = image_sha256(image)
    fixture_id = content_hash[:20]
    record = {
        "schema_version": GROUND_TRUTH_SCHEMA_VERSION,
        "fixture_id": fixture_id,
        "image_sha256": content_hash,
        "image": str(image),
        "group": normalized_group,
        "partial": any(event.region_index is not None for event in normalized_events),
        "events": [_ground_truth_payload(event) for event in normalized_events],
    }

    destination = Path(ground_truth_path)
    rows = read_jsonl(destination)
    replaced = False
    for index, row in enumerate(rows):
        if row.get("image_sha256") == content_hash or row.get("fixture_id") == fixture_id:
            rows[index] = record
            replaced = True
            break
    if not replaced:
        rows.append(record)
    write_jsonl(destination, rows)
    return record, replaced


def load_ground_truth(path: str | Path = DEFAULT_GROUND_TRUTH) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"No existe el ground truth: {source}")
    rows = read_jsonl(source)
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        fixture_id = str(row.get("fixture_id") or "").strip()
        image = str(row.get("image") or "").strip()
        group = str(row.get("group") or "development").strip()
        events = row.get("events")
        if not fixture_id or fixture_id in seen:
            raise ValueError(f"Fixture inválido o duplicado en la línea {index}: {fixture_id!r}.")
        if not image or not isinstance(events, list) or not events:
            raise ValueError(f"La línea {index} requiere image y al menos un evento.")
        normalized_events = [
            _ground_truth_payload(normalize_ground_truth_event(
                event.get("service_date", ""),
                event.get("mileage_km", ""),
                event.get("region_index"),
            ))
            for event in events
            if isinstance(event, dict)
        ]
        if len(normalized_events) != len(events):
            raise ValueError(f"Todos los eventos de la línea {index} deben ser objetos JSON.")
        seen.add(fixture_id)
        validated.append({
            **row,
            "fixture_id": fixture_id,
            "image": image,
            "group": group,
            "events": normalized_events,
        })
    return validated


def load_predictions(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"No existen las predicciones: {source}")
    text = source.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        payload = json.loads(text)
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise ValueError("El JSON de predicciones debe ser una lista de objetos.")
        return payload
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return read_jsonl(source)
        if isinstance(payload, dict):
            payload = payload.get("predictions") if "predictions" in payload else [payload]
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise ValueError("El JSON de predicciones debe ser una lista o contener una lista `predictions`.")
        return payload
    return read_jsonl(source)


def boxes_from_diagnostics(diagnostics: Iterable[Any], *, fallback: int) -> int:
    counts: list[int] = []
    for message in diagnostics:
        match = re.search(r"(\d+)\s+cuadros?\s+candidatos?", str(message), flags=re.IGNORECASE)
        if match:
            counts.append(int(match.group(1)))
    return counts[-1] if counts else fallback


def prediction_from_extraction(fixture: dict[str, Any], extraction: Any) -> dict[str, Any]:
    metadata = extraction.metadata if isinstance(getattr(extraction, "metadata", None), dict) else {}
    raw_events = metadata.get("maintenance_image_events") or []
    events: list[dict[str, Any]] = []
    for event in raw_events:
        service_date = event.get("service_date") if isinstance(event, dict) else None
        mileage = event.get("mileage") if isinstance(event, dict) else None
        events.append({
            "service_date": service_date.get("normalized_value") if isinstance(service_date, dict) else service_date,
            "mileage_km": mileage.get("normalized_value") if isinstance(mileage, dict) else mileage,
            "confidence_score": event.get("confidence_score", 0.0),
            "requires_human_review": bool(event.get("requires_human_review", True)),
            "region": event.get("region"),
            "region_index": event.get("region_index"),
        })
    diagnostics = metadata.get("diagnostics") or []
    return {
        "fixture_id": fixture["fixture_id"],
        "image": fixture["image"],
        "boxes_detected": boxes_from_diagnostics(diagnostics, fallback=len(events)),
        "events": events,
        "diagnostics": diagnostics,
    }


def run_local_predictions(
    fixtures: list[dict[str, Any]],
    *,
    handwriting_model_path: str | Path | None = None,
    handwriting_python: str | Path | None = None,
    debug_enabled: bool = False,
    debug_directory: str | Path | None = None,
    debug_run_prefix: str | None = None,
) -> list[dict[str, Any]]:
    # Import tardío: analizar predicciones guardadas no debe requerir Tesseract ni OpenCV.
    from app.services.extraction_service import OcrExtractor

    predictions: list[dict[str, Any]] = []
    mime_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    for fixture in fixtures:
        image = Path(fixture["image"]).expanduser()
        try:
            extractor = OcrExtractor(
                handwriting_model_path=handwriting_model_path,
                handwriting_python=handwriting_python,
                debug_enabled=debug_enabled,
                debug_directory=debug_directory or PROJECT_DIR / "debug_ocr",
                debug_run_id=(
                    f"{debug_run_prefix}-{fixture['fixture_id']}"
                    if debug_run_prefix else None
                ),
            )
            started = time.perf_counter()
            extraction = extractor.extract(str(image), mime_types.get(image.suffix.lower(), "application/octet-stream"))
            prediction = prediction_from_extraction(fixture, extraction)
            prediction["processing_seconds"] = round(time.perf_counter() - started, 4)
            predictions.append(prediction)
        except Exception as exc:  # El benchmark debe conservar los demás resultados y señalar el fixture fallido.
            predictions.append({
                "fixture_id": fixture["fixture_id"],
                "image": fixture["image"],
                "boxes_detected": 0,
                "events": [],
                "error": f"{type(exc).__name__}: {exc}",
            })
    return predictions


def _value(event: dict[str, Any], field: str) -> Any:
    value = event.get(field)
    if isinstance(value, dict):
        return value.get("normalized_value")
    if field == "mileage_km" and value is None:
        value = event.get("mileage")
        if isinstance(value, dict):
            return value.get("normalized_value")
    return value


def _normalized_date(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        return None


def _normalized_mileage(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).strip().replace(",", "").replace(".", "").replace(" ", "")
    return int(raw) if re.fullmatch(r"\d+", raw) else None


def _event_values(event: dict[str, Any]) -> tuple[str | None, int | None]:
    return _normalized_date(_value(event, "service_date")), _normalized_mileage(_value(event, "mileage_km"))


def _pair_score(expected: dict[str, Any], predicted: dict[str, Any], expected_index: int, predicted_index: int) -> float:
    expected_date, expected_mileage = _event_values(expected)
    predicted_date, predicted_mileage = _event_values(predicted)
    date_exact = expected_date == predicted_date
    mileage_exact = expected_mileage == predicted_mileage
    pair_exact = date_exact and mileage_exact
    return pair_exact * 100.0 + (date_exact + mileage_exact) * 10.0 - abs(expected_index - predicted_index) * 0.001


def _match_events(expected: list[dict[str, Any]], predicted: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Asigna uno a uno maximizando pares exactos, campos exactos y cercanía visual."""
    if not expected or not predicted:
        return []
    # Una libreta real normalmente tiene pocos cuadros. Evita crecimiento exponencial ante datos corruptos.
    if max(len(expected), len(predicted)) > 14:
        return [(index, index) for index in range(min(len(expected), len(predicted)))]

    if len(expected) <= len(predicted):
        @lru_cache(maxsize=None)
        def assign(expected_index: int, used_predictions: int) -> tuple[float, tuple[tuple[int, int], ...]]:
            if expected_index == len(expected):
                return 0.0, ()
            best_score = float("-inf")
            best_pairs: tuple[tuple[int, int], ...] = ()
            for predicted_index in range(len(predicted)):
                bit = 1 << predicted_index
                if used_predictions & bit:
                    continue
                tail_score, tail_pairs = assign(expected_index + 1, used_predictions | bit)
                score = _pair_score(expected[expected_index], predicted[predicted_index], expected_index, predicted_index) + tail_score
                if score > best_score:
                    best_score = score
                    best_pairs = ((expected_index, predicted_index), *tail_pairs)
            return best_score, best_pairs

        return list(assign(0, 0)[1])

    @lru_cache(maxsize=None)
    def assign_prediction(predicted_index: int, used_expected: int) -> tuple[float, tuple[tuple[int, int], ...]]:
        if predicted_index == len(predicted):
            return 0.0, ()
        best_score = float("-inf")
        best_pairs: tuple[tuple[int, int], ...] = ()
        for expected_index in range(len(expected)):
            bit = 1 << expected_index
            if used_expected & bit:
                continue
            tail_score, tail_pairs = assign_prediction(predicted_index + 1, used_expected | bit)
            score = _pair_score(expected[expected_index], predicted[predicted_index], expected_index, predicted_index) + tail_score
            if score > best_score:
                best_score = score
                best_pairs = ((expected_index, predicted_index), *tail_pairs)
        return best_score, best_pairs

    return sorted(assign_prediction(0, 0)[1])


def _requires_review(event: dict[str, Any], threshold: float) -> bool:
    if "requires_human_review" in event:
        return bool(event["requires_human_review"])
    if "accepted" in event:
        return not bool(event["accepted"])
    try:
        return float(event.get("confidence_score", event.get("confidence", 0.0))) < threshold
    except (TypeError, ValueError):
        return True


def evaluate_real_benchmark(
    ground_truth: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    *,
    threshold: float = 0.88,
) -> dict[str, Any]:
    prediction_by_id = {
        str(row.get("fixture_id")): row
        for row in predictions
        if isinstance(row, dict) and row.get("fixture_id")
    }
    boxes_detected = 0
    expected_total = 0
    detected_total = 0
    date_correct = 0
    mileage_correct = 0
    pair_correct = 0
    reviewed = 0
    accepted = 0
    false_accepts = 0
    image_results: list[dict[str, Any]] = []

    for fixture in ground_truth:
        expected = fixture["events"]
        prediction = prediction_by_id.get(str(fixture["fixture_id"]), {})
        all_predicted = prediction.get("events") if isinstance(prediction.get("events"), list) else []
        partial = bool(fixture.get("partial"))
        annotated_regions = {
            int(event["region_index"])
            for event in expected
            if event.get("region_index") is not None
        }
        predicted = (
            [event for event in all_predicted if event.get("region_index") in annotated_regions]
            if partial and annotated_regions
            else all_predicted
        )
        fixture_boxes = max(0, int(prediction.get("boxes_detected", len(predicted)) or 0))
        boxes_detected += fixture_boxes
        expected_total += len(expected)
        detected_total += len(predicted)

        assignments = _match_events(expected, predicted)
        assigned_expected = {item[0] for item in assignments}
        assigned_predicted = {item[1] for item in assignments}
        pair_details: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        correctness_by_prediction: dict[int, bool] = {}

        for expected_index, predicted_index in assignments:
            expected_date, expected_mileage = _event_values(expected[expected_index])
            predicted_date, predicted_mileage = _event_values(predicted[predicted_index])
            date_exact = expected_date == predicted_date
            mileage_exact = expected_mileage == predicted_mileage
            pair_exact = date_exact and mileage_exact
            date_correct += int(date_exact)
            mileage_correct += int(mileage_exact)
            pair_correct += int(pair_exact)
            correctness_by_prediction[predicted_index] = pair_exact
            detail = {
                "expected_index": expected_index + 1,
                "predicted_index": predicted_index + 1,
                "expected": {"service_date": expected_date, "mileage_km": expected_mileage},
                "predicted": {"service_date": predicted_date, "mileage_km": predicted_mileage},
                "date_exact": date_exact,
                "mileage_exact": mileage_exact,
                "pair_exact": pair_exact,
            }
            pair_details.append(detail)
            if not pair_exact:
                failures.append({"type": "field_mismatch", **detail})

        for expected_index, event in enumerate(expected):
            if expected_index not in assigned_expected:
                expected_date, expected_mileage = _event_values(event)
                failures.append({
                    "type": "missing_event",
                    "expected_index": expected_index + 1,
                    "expected": {"service_date": expected_date, "mileage_km": expected_mileage},
                })
        for predicted_index, event in enumerate(predicted):
            requires_review = _requires_review(event, threshold)
            reviewed += int(requires_review)
            accepted += int(not requires_review)
            is_correct = correctness_by_prediction.get(predicted_index, False)
            false_accepts += int(not requires_review and not is_correct)
            if predicted_index not in assigned_predicted:
                predicted_date, predicted_mileage = _event_values(event)
                failures.append({
                    "type": "extra_event",
                    "predicted_index": predicted_index + 1,
                    "predicted": {"service_date": predicted_date, "mileage_km": predicted_mileage},
                    "requires_human_review": requires_review,
                })

        if not partial and fixture_boxes != len(expected):
            failures.insert(0, {
                "type": "box_count_mismatch",
                "expected": len(expected),
                "detected": fixture_boxes,
            })
        if prediction.get("error"):
            failures.insert(0, {"type": "processing_error", "message": str(prediction["error"])})

        image_results.append({
            "fixture_id": fixture["fixture_id"],
            "image": fixture["image"],
            "boxes_detected": fixture_boxes,
            "expected_events": len(expected),
            "detected_events": len(predicted),
            "total_unverified_events": len(all_predicted) if partial else 0,
            "partial_ground_truth": partial,
            "processing_seconds": prediction.get("processing_seconds"),
            "matches": pair_details,
            "failures": failures,
        })

    failures_by_image = [row for row in image_results if row["failures"]]
    return {
        "boxes_detected": boxes_detected,
        "expected_events": expected_total,
        "detected_events": detected_total,
        "DATE_EXACT_ACCURACY": date_correct / expected_total if expected_total else 0.0,
        "MILEAGE_EXACT_ACCURACY": mileage_correct / expected_total if expected_total else 0.0,
        "PAIR_EXACT_ACCURACY": pair_correct / expected_total if expected_total else 0.0,
        "HUMAN_REVIEW_RATE": reviewed / detected_total if detected_total else 0.0,
        "FALSE_ACCEPT_RATE": false_accepts / accepted if accepted else 0.0,
        "accepted_events": accepted,
        "false_accepts": false_accepts,
        "images": len(ground_truth),
        "images_with_failures": len(failures_by_image),
        "failures_by_image": failures_by_image,
        "image_results": image_results,
    }
