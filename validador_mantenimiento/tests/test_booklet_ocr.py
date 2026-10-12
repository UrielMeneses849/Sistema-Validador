from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image, ImageDraw

from app.services.maintenance_image_recognizer import (
    BookletGridStrategy, BoundingBox, OcrToken, RecognitionCandidate,
    TesseractFieldOcrBackend, _region_ink_metrics, evaluate_consensus, parse_date_candidates, parse_native_mileage,
)


def test_orientation_checks_printed_labels_instead_of_trusting_stamp_osd():
    backend = TesseractFieldOcrBackend(SimpleNamespace())
    anchors = [OcrToken("Fecha", .95, BoundingBox(0, 0, 20, 10)),
               OcrToken("MANTENIMIENTO", .95, BoundingBox(30, 0, 80, 10))]
    backend.read_layout = Mock(side_effect=[[], [], [], anchors])
    assert backend.detect_orientation(Image.new("RGB", (100, 150))) == 270


def test_grid_recovers_obscured_labels_without_crossing_service_columns():
    boxes = [BoundingBox(100+x*300, 100+y*230, 370+x*300, 300+y*230)
             for y in range(2) for x in range(3)]
    tokens = [OcrToken("MANTENIMIENTO", .9, BoundingBox(100, 100, 260, 120)),
              OcrToken("Km/Millas", .9, BoundingBox(100, 250, 170, 270))]
    # Un sello rompió el contorno del cuadro superior izquierdo.
    regions = BookletGridStrategy().detect(tokens, width=1200, height=700, rectangles=boxes[1:])
    assert len(regions) == 6
    assert regions[0].box == boxes[0]
    for region in regions:
        assert region.box.contains(region.date_crop)
        assert region.box.contains(region.mileage_crop)
    assert BookletGridStrategy().detect(tokens, width=1200, height=700, rectangles=boxes[:1]) == []


def test_blank_slanted_rules_are_not_handwriting(tmp_path):
    image = Image.new("RGB", (500, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.line((0, 75, 499, 35), fill="black", width=2)
    path = tmp_path / "blank.png"
    image.save(path)
    metrics = _region_ink_metrics(path, (BoundingBox(0,0,250,100), BoundingBox(250,0,500,100)))
    assert metrics is not None and not metrics["has_content"]


def test_broken_crops_are_clamped_to_a_nonempty_image():
    box = BoundingBox(200, 300, 10, 20).clamp(100, 100)
    assert box == BoundingBox(99,99,100,100)


def test_date_ocr_handles_month_glyphs_and_separators_without_inventing_dates():
    for raw in ("24/0ct/2025", "24/6ct/2025", "24 / oct / 2025"):
        assert parse_date_candidates(raw)[0][0] == "2025-10-24"
    assert parse_date_candidates("27/04126")[0][0] == "2026-04-27"
    assert parse_date_candidates("31/02126") == []
    assert parse_date_candidates("2026") == []


def test_native_variants_cannot_auto_accept_a_handwritten_field():
    candidates = [RecognitionCandidate("10,241", 10241, 1, 1, variant, "apple_vision_local")
                  for variant in ("native_scale", "field_original", "service_context")]
    result = evaluate_consensus("mileage", candidates, attempted_variants=6)
    assert result.normalized_value == 10241
    assert result.confidence_score < .88
    assert "macOS" in result.selection_reason


def test_native_mileage_keeps_km_label_but_rejects_orders_and_phones():
    assert parse_native_mileage("94947 km/k")[0][0] == 94947
    assert parse_native_mileage("88,093 Km / Km")[0][0] == 88093
    assert parse_native_mileage("Repair Order No. 119001") == []
    assert parse_native_mileage("Tel. 229 923 9640") == []
    assert parse_native_mileage("15,08)") == []
