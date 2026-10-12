from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.services.maintenance_image_recognizer import parse_date_candidates, parse_mileage_candidates


def normalize_prediction(field: str, value: str) -> str | None:
    if field == "date":
        candidates = parse_date_candidates(value)
        return candidates[0][0] if candidates else None
    candidates = parse_mileage_candidates(value)
    return str(candidates[0][0]) if candidates else None


def calculate_metrics(rows: list[dict[str, Any]], *, threshold: float = 0.88) -> dict[str, float | int]:
    totals = defaultdict(int)
    correct = defaultdict(int)
    pair_groups: dict[str, list[bool]] = defaultdict(list)
    accepted = 0
    false_accepts = 0
    review = 0
    for row in rows:
        field = str(row["field"])
        expected = normalize_prediction(field, str(row["text"]))
        predicted = normalize_prediction(field, str(row.get("prediction", "")))
        is_correct = expected is not None and predicted == expected
        confidence = float(row.get("confidence", 0.0))
        is_accepted = bool(row.get("accepted", confidence >= threshold and predicted is not None))
        totals[field] += 1
        correct[field] += int(is_correct)
        pair_groups[str(row.get("page_group") or row.get("pair_id") or row.get("sample_id"))].append(is_correct)
        accepted += int(is_accepted)
        false_accepts += int(is_accepted and not is_correct)
        review += int(not is_accepted)
    complete_pairs = [values for values in pair_groups.values() if len(values) >= 2]
    return {
        "DATE_EXACT_ACCURACY": correct["date"] / totals["date"] if totals["date"] else 0.0,
        "MILEAGE_EXACT_ACCURACY": correct["mileage"] / totals["mileage"] if totals["mileage"] else 0.0,
        "PAIR_EXACT_ACCURACY": sum(all(values) for values in complete_pairs) / len(complete_pairs) if complete_pairs else 0.0,
        "HUMAN_REVIEW_RATE": review / len(rows) if rows else 0.0,
        "FALSE_ACCEPT_RATE": false_accepts / accepted if accepted else 0.0,
        "samples": len(rows),
        "accepted": accepted,
        "false_accepts": false_accepts,
    }
