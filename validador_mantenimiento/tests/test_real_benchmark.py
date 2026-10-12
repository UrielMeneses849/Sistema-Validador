from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from training.evaluate_real_benchmark import main as evaluate_main
from training.label_real_fixture import main as label_main
from training.real_benchmark import (
    evaluate_real_benchmark,
    load_ground_truth,
    prediction_from_extraction,
    save_ground_truth,
)


def _image(path: Path) -> Path:
    Image.new("RGB", (80, 60), "white").save(path)
    return path


def test_label_fixture_stores_multiple_events_outside_training_data_and_updates_by_hash(tmp_path):
    image = _image(tmp_path / "book.jpg")
    manifest = tmp_path / "benchmark_data" / "ground_truth.jsonl"

    first, replaced = save_ground_truth(
        image,
        [("2025-10-24", "88,913"), ("2026-05-10", 100_000)],
        ground_truth_path=manifest,
    )
    assert replaced is False
    assert first["events"] == [
        {"service_date": "2025-10-24", "mileage_km": 88913},
        {"service_date": "2026-05-10", "mileage_km": 100000},
    ]
    assert "training_data" not in manifest.parts

    second, replaced = save_ground_truth(
        image,
        [("2025-10-24", 88913)],
        ground_truth_path=manifest,
    )
    assert replaced is True
    assert second["fixture_id"] == first["fixture_id"]
    assert len(load_ground_truth(manifest)) == 1
    assert load_ground_truth(manifest)[0]["events"] == [
        {"service_date": "2025-10-24", "mileage_km": 88913}
    ]


def test_label_cli_supports_repeated_noninteractive_events(tmp_path, capsys):
    image = _image(tmp_path / "fixture.png")
    manifest = tmp_path / "truth.jsonl"
    label_main([
        str(image),
        "--ground-truth", str(manifest),
        "--event", "2025-10-24", "88913",
        "--event", "2026-05-10", "100000",
    ])
    summary = json.loads(capsys.readouterr().out)
    assert summary["events"] == 2
    assert summary["replaced"] is False
    assert len(load_ground_truth(manifest)[0]["events"]) == 2


def test_partial_region_ground_truth_evaluates_only_the_verified_box(tmp_path, capsys):
    image = _image(tmp_path / "fixture.png")
    manifest = tmp_path / "truth.jsonl"
    label_main([
        str(image),
        "--ground-truth", str(manifest),
        "--region-event", "3", "2025-05-28", "36389",
    ])
    capsys.readouterr()
    fixture = load_ground_truth(manifest)[0]
    assert fixture["partial"] is True
    assert fixture["events"][0]["region_index"] == 3

    report = evaluate_real_benchmark([fixture], [{
        "fixture_id": fixture["fixture_id"],
        "boxes_detected": 6,
        "events": [
            {"region_index": 1, "service_date": "2020-01-01", "mileage_km": 1, "requires_human_review": False},
            {"region_index": 3, "service_date": "2025-05-28", "mileage_km": 36389, "requires_human_review": True},
        ],
    }])
    assert report["expected_events"] == 1
    assert report["detected_events"] == 1
    assert report["PAIR_EXACT_ACCURACY"] == 1.0
    assert report["FALSE_ACCEPT_RATE"] == 0.0
    assert report["images_with_failures"] == 0


def test_invalid_ground_truth_is_rejected(tmp_path):
    image = _image(tmp_path / "fixture.png")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        save_ground_truth(image, [("24/10/2025", 88913)], ground_truth_path=tmp_path / "truth.jsonl")
    with pytest.raises(ValueError, match="entero"):
        save_ground_truth(image, [("2025-10-24", "88?913")], ground_truth_path=tmp_path / "truth.jsonl")


def test_real_benchmark_reports_detection_accuracy_review_false_accepts_and_failures():
    ground_truth = [
        {
            "fixture_id": "a",
            "image": "/fixtures/a.jpg",
            "events": [
                {"service_date": "2025-10-24", "mileage_km": 88913},
                {"service_date": "2026-05-10", "mileage_km": 100000},
            ],
        },
        {
            "fixture_id": "b",
            "image": "/fixtures/b.jpg",
            "events": [{"service_date": "2024-01-02", "mileage_km": 50000}],
        },
    ]
    predictions = [
        {
            "fixture_id": "a",
            "boxes_detected": 3,
            "events": [
                {
                    "service_date": "2025-10-24", "mileage_km": 88913,
                    "requires_human_review": False,
                },
                {
                    "service_date": "2026-05-10", "mileage_km": 100800,
                    "requires_human_review": True,
                },
                {
                    "service_date": "2023-12-12", "mileage_km": 1,
                    "requires_human_review": False,
                },
            ],
        },
        {"fixture_id": "b", "boxes_detected": 1, "events": []},
    ]

    report = evaluate_real_benchmark(ground_truth, predictions)
    assert report["boxes_detected"] == 4
    assert report["expected_events"] == 3
    assert report["detected_events"] == 3
    assert report["DATE_EXACT_ACCURACY"] == pytest.approx(2 / 3)
    assert report["MILEAGE_EXACT_ACCURACY"] == pytest.approx(1 / 3)
    assert report["PAIR_EXACT_ACCURACY"] == pytest.approx(1 / 3)
    assert report["HUMAN_REVIEW_RATE"] == pytest.approx(1 / 3)
    assert report["FALSE_ACCEPT_RATE"] == pytest.approx(1 / 2)
    assert report["images_with_failures"] == 2
    assert {failure["type"] for failure in report["failures_by_image"][0]["failures"]} >= {
        "box_count_mismatch", "field_mismatch", "extra_event"
    }
    assert any(
        failure["type"] == "missing_event"
        for failure in report["failures_by_image"][1]["failures"]
    )


def test_event_matching_is_independent_of_prediction_order():
    ground_truth = [{
        "fixture_id": "a", "image": "a.jpg", "events": [
            {"service_date": "2025-01-01", "mileage_km": 100},
            {"service_date": "2026-01-01", "mileage_km": 200},
        ],
    }]
    predictions = [{
        "fixture_id": "a", "boxes_detected": 2, "events": [
            {"service_date": "2026-01-01", "mileage_km": 200, "accepted": True},
            {"service_date": "2025-01-01", "mileage_km": 100, "accepted": True},
        ],
    }]
    report = evaluate_real_benchmark(ground_truth, predictions)
    assert report["PAIR_EXACT_ACCURACY"] == 1.0
    assert report["FALSE_ACCEPT_RATE"] == 0.0
    assert report["images_with_failures"] == 0


def test_prediction_adapter_counts_candidate_boxes_from_diagnostics():
    fixture = {"fixture_id": "a", "image": "fixture.jpg"}
    extraction = SimpleNamespace(metadata={
        "diagnostics": ["Layout grayscale: 12 tokens, 4 rectángulos y 3 cuadros candidatos."],
        "maintenance_image_events": [{
            "service_date": {"normalized_value": "2025-10-24"},
            "mileage": {"normalized_value": 88913},
            "confidence_score": 0.91,
            "requires_human_review": False,
            "region": {"x0": 1, "y0": 2, "x1": 3, "y1": 4},
        }],
    })
    prediction = prediction_from_extraction(fixture, extraction)
    assert prediction["boxes_detected"] == 3
    assert prediction["events"][0]["service_date"] == "2025-10-24"
    assert prediction["events"][0]["mileage_km"] == 88913


def test_evaluation_cli_accepts_saved_predictions_and_writes_report(tmp_path, capsys):
    image = _image(tmp_path / "fixture.jpg")
    manifest = tmp_path / "truth.jsonl"
    record, _ = save_ground_truth(image, [("2025-10-24", 88913)], ground_truth_path=manifest)
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text(json.dumps({
        "fixture_id": record["fixture_id"],
        "boxes_detected": 1,
        "events": [{
            "service_date": "2025-10-24",
            "mileage_km": 88913,
            "confidence_score": 0.95,
        }],
    }) + "\n", encoding="utf-8")
    output = tmp_path / "report.json"

    evaluate_main([
        "--ground-truth", str(manifest),
        "--predictions", str(predictions),
        "--output", str(output),
    ])
    report = json.loads(capsys.readouterr().out)
    assert report["PAIR_EXACT_ACCURACY"] == 1.0
    assert report["HUMAN_REVIEW_RATE"] == 0.0
    assert report["FALSE_ACCEPT_RATE"] == 0.0
    assert json.loads(output.read_text(encoding="utf-8")) == report
