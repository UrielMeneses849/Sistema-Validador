from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

from app.services.document_parser import parse_document
from app.services.extraction_service import ExtractionResult
from app.services.maintenance_image_recognizer import (
    BoundingBox,
    GenericServiceBoxStrategy,
    LocalMaintenanceImageRecognizer,
    OcrReading,
    OcrToken,
    RecognitionCandidate,
    _is_date_label,
    _is_mileage_label,
    _region_ink_metrics,
    evaluate_consensus,
    parse_date_candidates,
    parse_mileage_candidates,
)
from app.services.training_dataset_service import record_human_verified_event
from training.export_dataset import split_by_page
from training.metrics import calculate_metrics


class FakeFieldBackend:
    name = "fake-local"

    def __init__(self, pairs: list[tuple[str, str]], *, rotation: int | None = None, conflicts: bool = False) -> None:
        self.pairs = pairs
        self.rotation = rotation
        self.conflicts = conflicts
        self.calls = {"date": 0, "mileage": 0}

    def detect_orientation(self, _image: Image.Image) -> int | None:
        return self.rotation

    def read_layout(self, _image: Image.Image, *, variant: str) -> list[OcrToken]:
        tokens: list[OcrToken] = []
        for index in range(len(self.pairs)):
            top = 80 + index * 360
            tokens.extend([
                OcrToken("Fecha", 0.99, BoundingBox(90, top, 190, top + 36)),
                OcrToken("Kilometraje", 0.99, BoundingBox(90, top + 100, 300, top + 138)),
            ])
        return tokens

    def read_field(self, _image: Image.Image, *, field_type: str, variant: str) -> OcrReading:
        index = self.calls[field_type] // 6
        self.calls[field_type] += 1
        if index >= len(self.pairs):
            return OcrReading("", 0.0, self.name)
        value = self.pairs[index][0 if field_type == "date" else 1]
        if self.conflicts and variant == "adaptive_threshold" and field_type == "mileage":
            value = "60?72"
        return OcrReading(value, 0.97, self.name)


def test_parsers_cover_expected_date_and_mileage_formats():
    expected_date = "2026-05-10"
    for raw in ("10/05/2026", "10-05-26", "10 MAY 2026", "Mayo 10, 2026"):
        assert parse_date_candidates(raw)[0][0] == expected_date
    assert parse_date_candidates("2026/10/21")[0][0] == "2026-10-21"
    assert parse_date_candidates("24/oct/2025")[0][0] == "2025-10-24"
    for raw in ("10,000", "10.000 km", "10000km", "36 389"):
        assert parse_mileage_candidates(raw)[0][0] in {10_000, 36_389}


def test_consensus_is_conservative_with_one_reading_and_strong_with_three():
    candidate = RecognitionCandidate("60972", 60972, 0.98, 1.0, "original", "fake")
    one = evaluate_consensus("mileage", [candidate], attempted_variants=6)
    three = evaluate_consensus(
        "mileage",
        [
            candidate,
            replace(candidate, variant="grayscale"),
            replace(candidate, variant="high_contrast"),
        ],
        attempted_variants=6,
    )
    assert one.confidence_score < 0.88
    assert three.confidence_score >= 0.88


def test_generic_strategy_detects_multiple_service_boxes_and_pairs_labels():
    backend = FakeFieldBackend([("24/10/2025", "88913"), ("10/05/2026", "100000")])
    regions = GenericServiceBoxStrategy().detect(
        backend.read_layout(Image.new("RGB", (1800, 1200)), variant="original"),
        width=1800,
        height=1200,
        rectangles=[],
    )
    assert len(regions) == 2
    assert regions[0].date_crop.y0 < regions[1].date_crop.y0


def test_local_pipeline_creates_multiple_events_and_ignores_an_empty_box(tmp_path):
    source = tmp_path / "book.jpg"
    Image.new("RGB", (1800, 1200), "white").save(source)
    backend = FakeFieldBackend([
        ("24/10/2025", "88913"),
        ("10/05/2026", "100000"),
        ("", ""),
    ], conflicts=True)
    result = LocalMaintenanceImageRecognizer(
        backend,
        evidence_directory=tmp_path / "evidence",
    ).analyze(source)
    assert len(result.events) == 2
    assert result.events[0].service_date.normalized_value == "2025-10-24"
    assert result.events[0].mileage.normalized_value == 88913
    assert result.events[0].confidence_score >= 0.88
    assert (tmp_path / "evidence" / result.events[0].service_date.crop_id).is_file()
    assert result.external_fallback_used is False


def test_rotation_is_reported_and_does_not_modify_original(tmp_path):
    source = tmp_path / "rotated.png"
    Image.new("RGB", (1800, 1200), "white").save(source)
    before = source.read_bytes()
    result = LocalMaintenanceImageRecognizer(
        FakeFieldBackend([("24/10/2025", "88913")], rotation=90),
        evidence_directory=tmp_path / "evidence",
    ).analyze(source)
    assert len(result.events) == 1
    assert any("90 grados" in message for message in result.diagnostics)
    assert source.read_bytes() == before


def test_required_printed_label_spellings_are_recognized():
    box = BoundingBox(0, 0, 100, 30)
    assert all(_is_date_label(OcrToken(value, 0.9, box)) for value in ("Fecha", "DATE"))
    assert all(
        _is_mileage_label(OcrToken(value, 0.9, box))
        for value in ("Kilometraje", "Kilometraje:", "KM", "Km", "Km/Millas", "Km / Km")
    )


def test_debug_mode_writes_visual_stages_crops_variants_and_raw_attempts(tmp_path):
    source = tmp_path / "book.jpg"
    Image.new("RGB", (1800, 1200), "white").save(source)
    debug_root = tmp_path / "debug_ocr"
    result = LocalMaintenanceImageRecognizer(
        FakeFieldBackend([("24/10/2025", "88913")]),
        evidence_directory=tmp_path / "evidence",
        debug_enabled=True,
        debug_directory=debug_root,
        debug_run_id="document-42",
    ).analyze(source)

    output = debug_root / "document-42"
    assert result.debug_directory == str(output.resolve())
    for relative in (
        "original-oriented.png",
        "perspective-corrected.png",
        "bounding-boxes.png",
        "page-variants/original.png",
        "service-001/service-box.png",
        "service-001/date/original.png",
        "service-001/date/adaptive_threshold.png",
        "service-001/mileage/shadow_normalized.png",
        "layout.json",
        "ocr-report.json",
    ):
        assert (output / relative).is_file(), relative
    report = json.loads((output / "ocr-report.json").read_text(encoding="utf-8"))
    assert report["boxes_detected"] == 1
    assert report["detected_events"] == 1
    attempts = report["regions"][0]["ocr_attempts"]
    assert {item["variant"] for item in attempts["date"]} >= {
        "original", "grayscale", "high_contrast", "adaptive_threshold", "shadow_normalized"
    }
    assert attempts["mileage"][0]["raw_text"] == "88913"


def test_disabled_debug_mode_creates_no_debug_directory(tmp_path):
    source = tmp_path / "book.jpg"
    Image.new("RGB", (1800, 1200), "white").save(source)
    debug_root = tmp_path / "debug_ocr"
    result = LocalMaintenanceImageRecognizer(
        FakeFieldBackend([("24/10/2025", "88913")]),
        evidence_directory=tmp_path / "evidence",
        debug_enabled=False,
        debug_directory=debug_root,
    ).analyze(source)
    assert result.debug_directory is None
    assert not debug_root.exists()


def test_ink_detector_ignores_printed_lines_but_keeps_filled_fields(tmp_path):
    source = tmp_path / "fields.png"
    image = Image.new("RGB", (700, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.line((10, 80, 330, 80), fill="black", width=2)
    draw.line((360, 80, 680, 80), fill="black", width=2)
    image.save(source)
    crops = (BoundingBox(10, 20, 330, 110), BoundingBox(360, 20, 680, 110))
    assert _region_ink_metrics(source, crops)["has_content"] is False

    font = ImageFont.load_default(size=34)
    draw.text((40, 35), "28/05/2025", fill="black", font=font)
    draw.text((400, 35), "36389", fill="black", font=font)
    image.save(source)
    assert _region_ink_metrics(source, crops)["has_content"] is True


def test_specialized_result_maps_each_pair_to_a_normal_service_event():
    field = {
        "field_type": "date", "raw_value": "24/10/2025", "normalized_value": "2025-10-24",
        "confidence_score": 0.97, "confidence": "high", "ambiguous": False,
        "crop_id": "doc/date.png", "candidates": [],
    }
    mileage = {
        **field, "field_type": "mileage", "raw_value": "88913", "normalized_value": 88913,
        "crop_id": "doc/mileage.png",
    }
    parsed = parse_document(ExtractionResult(
        method="local_maintenance_image",
        has_usable_text=True,
        metadata={"maintenance_image_events": [
            {"service_date": field, "mileage": mileage, "confidence_score": 0.97,
             "requires_human_review": False, "warnings": [], "region": {"x0": 0, "y0": 0, "x1": 10, "y1": 10}},
            {"service_date": {**field, "normalized_value": "2026-05-10"},
             "mileage": {**mileage, "normalized_value": 100000}, "confidence_score": 0.97,
             "requires_human_review": False, "warnings": [], "region": {"x0": 0, "y0": 20, "x1": 10, "y1": 30}},
        ]},
    ))
    assert len(parsed.service_events) == 2
    assert all(event.resets_maintenance_interval is True for event in parsed.service_events)
    assert parsed.service_events[0].service_date.evidence("local")["crop_id"] == "doc/date.png"


def test_human_confirmations_are_deduplicated_and_page_splits_do_not_leak(tmp_path, monkeypatch):
    import app.services.training_dataset_service as dataset_service

    evidence_root = tmp_path / "evidence"
    training_root = tmp_path / "training_data"
    crop = evidence_root / "doc" / "field.png"
    crop.parent.mkdir(parents=True)
    Image.new("L", (120, 40), "white").save(crop)
    monkeypatch.setattr(dataset_service, "EVIDENCE_CROP_DIR", evidence_root)
    monkeypatch.setattr(dataset_service, "TRAINING_DATA_DIR", training_root)
    monkeypatch.setattr(dataset_service, "ANNOTATIONS_FILE", training_root / "annotations" / "verified.jsonl")
    event = SimpleNamespace(
        user_confirmed=True,
        service_date=date(2025, 10, 24),
        mileage_km=88913,
        document_id=42,
        source_page=1,
        field_evidence={
            "combined_confidence": 1.0,
            "date": {"crop_id": "doc/field.png", "raw_value": "24/10/2025", "confidence_score": 0.7, "candidates": []},
            "mileage_km": {"crop_id": "doc/field.png", "raw_value": "88913", "confidence_score": 0.7, "candidates": []},
        },
    )
    assert len(record_human_verified_event(event)) == 2
    assert record_human_verified_event(event) == []
    annotations = [json.loads(line) for line in dataset_service.ANNOTATIONS_FILE.read_text().splitlines()]
    assert len(annotations) == 2
    assert all(row["human_confirmed"] for row in annotations)

    records = [
        {"sample_id": f"{group}-{field}", "page_group": group, "field": field}
        for group in ("page-a", "page-b", "page-c", "page-d")
        for field in ("date", "mileage")
    ]
    splits = split_by_page(records, seed=7)
    locations = {
        row["page_group"]: split
        for split, rows in splits.items()
        for row in rows
    }
    assert len(locations) == 4
    for group in locations:
        assert sum(any(row["page_group"] == group for row in rows) for rows in splits.values()) == 1


def test_problem_metrics_prioritize_false_acceptance_and_exact_pairs():
    metrics = calculate_metrics([
        {"field": "date", "text": "2025-10-24", "prediction": "24/10/2025", "confidence": 0.95, "page_group": "a"},
        {"field": "mileage", "text": "88913", "prediction": "88913", "confidence": 0.95, "page_group": "a"},
        {"field": "mileage", "text": "100000", "prediction": "100800", "confidence": 0.92, "page_group": "b"},
        {"field": "date", "text": "2026-05-10", "prediction": "", "confidence": 0.1, "page_group": "b"},
    ])
    assert metrics["DATE_EXACT_ACCURACY"] == 0.5
    assert metrics["MILEAGE_EXACT_ACCURACY"] == 0.5
    assert metrics["PAIR_EXACT_ACCURACY"] == 0.5
    assert metrics["FALSE_ACCEPT_RATE"] == 1 / 3
    assert metrics["HUMAN_REVIEW_RATE"] == 0.25


def test_confirmation_serves_crop_saves_dataset_and_recalculates_val002(client, tmp_path, monkeypatch):
    import app.services.training_dataset_service as dataset_service
    from app.database.database import SessionLocal
    from app.models.service_event import ServiceEvent

    vehicle = client.post("/api/vehicles", json={
        "internal_number": "IMG-CONFIRM-01", "plate": "IMG001", "brand": "Kia",
        "model": "Rio", "year": 2025, "current_odometer": 90_000,
        "vehicle_condition": "used", "initial_odometer": 0,
    }).json()
    uploaded = client.post(
        "/api/documents/upload",
        data={"vehicle_id": vehicle["id"]},
        files={"file": ("book.png", _png_bytes(), "image/png")},
    ).json()
    evidence_root = tmp_path / "evidence"
    crop = evidence_root / "book" / "date.png"
    crop.parent.mkdir(parents=True)
    Image.new("L", (100, 35), "white").save(crop)
    training_root = tmp_path / "training"
    monkeypatch.setattr(dataset_service, "EVIDENCE_CROP_DIR", evidence_root)
    monkeypatch.setattr(dataset_service, "TRAINING_DATA_DIR", training_root)
    monkeypatch.setattr(dataset_service, "ANNOTATIONS_FILE", training_root / "annotations" / "verified.jsonl")
    with SessionLocal() as db:
        event = ServiceEvent(
            document_id=uploaded["id"], vehicle_id=vehicle["id"], source_page=1,
            extraction_method="local_maintenance_image", service_date=date(2025, 10, 24),
            service_date_raw="24/10/2025", mileage_km=88_913, mileage_raw="88913",
            service_category="preventive_maintenance", service_type="Mantenimiento preventivo",
            resets_maintenance_interval=True, confidence="medium", requires_human_review=True,
            field_evidence={
                "combined_confidence": 0.75, "base_requires_human_review": True,
                "date": {"crop_id": "book/date.png", "raw_value": "24/10/2025", "candidates": []},
                "mileage_km": {"crop_id": "book/date.png", "raw_value": "88913", "candidates": []},
            }, warnings=["Confirma los campos."],
        )
        db.add(event)
        db.commit()
        event_id = event.id

    crop_response = client.get(f"/api/service-events/{event_id}/evidence/date")
    assert crop_response.status_code == 200
    confirmed = client.post(f"/api/service-events/{event_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["user_confirmed"] is True
    assert confirmed.json()["requires_human_review"] is False
    annotations = dataset_service.ANNOTATIONS_FILE.read_text(encoding="utf-8").splitlines()
    assert len(annotations) == 2


def _png_bytes() -> bytes:
    from io import BytesIO

    output = BytesIO()
    Image.new("RGB", (320, 180), "white").save(output, format="PNG")
    return output.getvalue()
