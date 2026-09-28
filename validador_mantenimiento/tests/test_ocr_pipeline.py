from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.services.document_parser import (
    classify_service_event,
    extract_mileage,
    extract_spatial_date,
    extract_spatial_mileage,
    parse_document,
)
from app.services.extraction_service import ExtractedPage, ExtractionResult, LayoutWord, OcrUnavailableError
from app.services.image_preprocessing import ImageValidationError, inspect_image_content, preprocess_image


def image_bytes(image_format: str = "JPEG", size: tuple[int, int] = (320, 180)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "white").save(output, format=image_format)
    return output.getvalue()


def create_vehicle(client: TestClient, number: str) -> int:
    response = client.post("/api/vehicles", json={
        "internal_number": number, "plate": number[-7:], "brand": "Toyota",
        "model": "Hiace", "year": 2024, "current_odometer": 0,
    })
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.parametrize("raw", ["10,750 km", "10.750 km", "10750 KM"])
def test_mileage_formats_require_semantic_context(raw: str):
    field = extract_mileage([(1, f"Kilometraje registrado: {raw}")])
    assert field.normalized_value == 10750
    assert field.confidence_score >= 0.88
    assert extract_mileage([(1, "Folio interno 10750")]).normalized_value is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("KMS: 117658", 117658),
        ("Kms. 46145", 46145),
        ("KM: 34731", 34731),
        ("Kms. 93952", 93952),
        ("KM: 105849", 105849),
    ],
)
def test_mileage_accepts_km_and_kms_labels(text: str, expected: int):
    field = extract_mileage([(1, text)])
    assert field.normalized_value == expected
    assert field.confidence_score >= 0.88


def test_kms_column_is_spatially_linked_to_value_below():
    words = [
        LayoutWord("Kms.", 1, 396.5, 413.5, 199.9, 206.8),
        LayoutWord("46145", 1, 396.5, 416.4, 211.5, 218.6),
        LayoutWord("Asesor", 1, 470.0, 500.0, 199.9, 206.8),
    ]
    field = extract_spatial_mileage(words)
    assert field.normalized_value == 46145
    assert field.confidence_score >= 0.88


def test_repeated_service_date_outweighs_single_document_date():
    words = [
        LayoutWord("Fecha", 1, 480.0, 515.0, 50.0, 57.0),
        LayoutWord("13/12/2024", 1, 522.0, 557.0, 50.0, 57.0),
        LayoutWord("Fecha", 1, 70.0, 110.0, 292.0, 299.0),
        LayoutWord("12/12/2024", 1, 120.0, 155.0, 292.0, 299.0),
        LayoutWord("Fecha", 2, 110.0, 163.0, 188.0, 195.0),
        LayoutWord("12/12/2024", 2, 173.0, 208.0, 188.0, 195.0),
    ]

    field = extract_spatial_date(words)

    assert field.normalized_value == date(2024, 12, 12)
    assert field.confidence == "high"
    assert field.ambiguous is False


def test_scheduled_service_kms_is_not_used_as_actual_odometer():
    field = extract_mileage([(1, "Servicio de Mantenimiento de 6,000 Kms.")])
    assert field.normalized_value is None


@pytest.mark.parametrize(
    "description",
    [
        "1.35 SERVICIO DE 12,000 KM UNIDAD DE SERVICIO",
        "Unidad de servicio E48 S24SILVERADO 2.7 4X4 24",
        "Unidad de servicio E48 S36SILVERADO 2.7 4X4 24",
    ],
)
def test_dealer_scheduled_service_descriptions_restart_interval(description: str):
    category, service_type, resets, review, warnings = classify_service_event(
        works=[],
        description=description,
        document_type="comprobante_servicio",
    )

    assert category == "preventive_maintenance"
    assert service_type == "Mantenimiento preventivo"
    assert resets is True
    assert review is False
    assert warnings == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("7 de enero de 2024", "2024-01-07"),
        ("07-febrero-2024", "2024-02-07"),
        ("07.02.2024", "2024-02-07"),
        ("07/02/24", "2024-02-07"),
        ("2024-07-03T16:19:00", "2024-07-03"),
    ],
)
def test_spanish_and_numeric_date_formats(raw: str, expected: str | None):
    result = parse_document(ExtractionResult(
        method="tesseract",
        pages=[ExtractedPage(1, f"MANTENIMIENTO PREVENTIVO\nFecha: {raw}\nOdómetro: 10,750 km\nCAMBIO DE ACEITE")],
        has_usable_text=True,
    ))
    value = result.service_events[0].service_date.normalized_value
    assert (value.isoformat() if value else None) == expected


def test_multiple_dates_are_preserved_as_candidates_and_require_review():
    parsed = parse_document(ExtractionResult(
        method="tesseract",
        pages=[ExtractedPage(1, "MANTENIMIENTO\nFecha 01/02/2024\nFecha 03/04/2024\nOdómetro 10,750 km\nCAMBIO DE ACEITE")],
        has_usable_text=True,
    ))
    event = parsed.service_events[0]
    assert event.service_date.ambiguous is True
    assert len(event.service_date.candidates) == 2
    assert event.requires_human_review is True


def test_invoice_iso_issue_and_stamping_timestamps_resolve_to_the_same_date():
    parsed = parse_document(ExtractionResult(
        method="pdf_text",
        pages=[ExtractedPage(
            1,
            "Lugar y Fecha de Expedición\n"
            "SAN FRANCISCO DE CAMPECHE CAMP,\n"
            "2024-07-03T16:19:00\n"
            "Kilometraje 11,695\n"
            "SERVICIO DE 12,000 KM\n"
            "Fecha de timbrado del CFDI: 2024-07-03T17:24:13",
        )],
        has_usable_text=True,
    ))

    event = parsed.service_events[0]
    assert event.service_date.normalized_value == date(2024, 7, 3)
    assert event.service_category == "preventive_maintenance"
    assert event.resets_maintenance_interval is True
    assert event.requires_human_review is False
    assert event.service_date.raw_value == "2024-07-03T16:19:00"
    assert event.service_date.ambiguous is False


def test_low_token_confidence_does_not_become_high_document_confidence():
    text = "MANTENIMIENTO\nFecha 23/03/2024\nOdómetro 10,750 km\nCAMBIO DE ACEITE"
    words = [
        LayoutWord("23/03/2024", 1, 0, 100, 0, 20, 0.35),
        LayoutWord("10,750", 1, 0, 100, 25, 45, 0.42),
    ]
    event = parse_document(ExtractionResult(
        method="tesseract", pages=[ExtractedPage(1, text)], words=words, has_usable_text=True,
    )).service_events[0]
    assert event.confidence != "high"
    assert event.requires_human_review is True


def test_image_preprocessing_uses_a_new_grayscale_copy(tmp_path):
    source = tmp_path / "source.webp"
    destination = tmp_path / "prepared.png"
    Image.new("RGB", (500, 300), "white").save(source, format="WEBP")
    preprocess_image(source, destination)
    with Image.open(destination) as prepared:
        assert prepared.mode == "L"
        assert max(prepared.size) >= 1000
    with Image.open(source) as original:
        assert original.mode == "RGB"
        assert original.size == (500, 300)


def test_corrupt_and_excessive_pixel_images_are_rejected(monkeypatch):
    with pytest.raises(ImageValidationError, match="corrupto"):
        inspect_image_content(b"not-an-image")
    monkeypatch.setattr("app.services.image_preprocessing.OCR_MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ImageValidationError, match="límite seguro"):
        inspect_image_content(image_bytes(size=(20, 20)))


def test_upload_rejects_fake_image_and_configured_size_limit(client: TestClient, monkeypatch):
    vehicle_id = create_vehicle(client, "OCR-FILE-01")
    fake = client.post("/api/documents/upload", data={"vehicle_id": vehicle_id}, files={"file": ("fake.jpg", b"not-jpeg", "image/jpeg")})
    assert fake.status_code == 422
    assert "corrupto" in fake.json()["detail"]

    monkeypatch.setattr("app.services.document_service.OCR_MAX_FILE_SIZE_MB", 0)
    oversized = client.post("/api/documents/upload", data={"vehicle_id": vehicle_id}, files={"file": ("valid.jpg", image_bytes(), "image/jpeg")})
    assert oversized.status_code == 422
    assert "límite" in oversized.json()["detail"]


def test_fake_ocr_is_persisted_with_field_evidence(client: TestClient, monkeypatch):
    vehicle_id = create_vehicle(client, "OCR-FAKE-01")
    uploaded = client.post("/api/documents/upload", data={"vehicle_id": vehicle_id}, files={"file": ("service.jpg", image_bytes(), "image/jpeg")})
    assert uploaded.status_code == 201

    fake_result = ExtractionResult(
        method="tesseract",
        pages=[ExtractedPage(1, "MANTENIMIENTO PREVENTIVO\nFecha 23/03/2024\nOdómetro 10,750 km\nCAMBIO DE ACEITE Y FILTRO")],
        has_usable_text=True,
        language="spa+eng",
        metadata={"provider": "fake-tesseract", "average_token_confidence": 0.95},
    )
    monkeypatch.setattr("app.services.extraction_service.OcrExtractor.extract", lambda self, path, mime: fake_result)
    response = client.post(f"/api/documents/{uploaded.json()['id']}/analyze")
    assert response.status_code == 200, response.text
    payload = response.json()
    event = payload["service_events"][0]
    assert event["service_date"] == "2024-03-23"
    assert event["mileage_km"] == 10750
    assert event["field_evidence"]["date"]["confidence_score"] >= 0.88
    assert event["field_evidence"]["mileage_km"]["candidates"]
    assert payload["extracted_fields"]["ocr"]["provider"] == "fake-tesseract"


def test_missing_tesseract_returns_actionable_503(client: TestClient, monkeypatch):
    vehicle_id = create_vehicle(client, "OCR-503-01")
    uploaded = client.post("/api/documents/upload", data={"vehicle_id": vehicle_id}, files={"file": ("service.png", image_bytes("PNG"), "image/png")})
    monkeypatch.setattr(
        "app.services.extraction_service.OcrExtractor._load_engine",
        lambda self: (_ for _ in ()).throw(OcrUnavailableError("Instala Tesseract y los idiomas spa+eng.")),
    )
    response = client.post(f"/api/documents/{uploaded.json()['id']}/analyze")
    assert response.status_code == 503
    assert "Tesseract" in response.json()["detail"]


def test_manual_correction_is_marked_and_blocks_ocr_reprocessing(client: TestClient, monkeypatch):
    vehicle_id = create_vehicle(client, "OCR-MANUAL-01")
    uploaded = client.post("/api/documents/upload", data={"vehicle_id": vehicle_id}, files={"file": ("service.jpg", image_bytes(), "image/jpeg")})
    fake_result = ExtractionResult(
        method="tesseract",
        pages=[ExtractedPage(1, "MANTENIMIENTO\nFecha 23/03/2024\nOdómetro 10,750 km\nCAMBIO DE ACEITE")],
        has_usable_text=True,
    )
    monkeypatch.setattr("app.services.extraction_service.OcrExtractor.extract", lambda self, path, mime: fake_result)
    analysis = client.post(f"/api/documents/{uploaded.json()['id']}/analyze").json()
    event_id = analysis["service_events"][0]["id"]
    corrected = client.put(f"/api/service-events/{event_id}", json={"mileage_km": 10800})
    assert corrected.json()["user_confirmed"] is True
    assert corrected.json()["field_evidence"]["manually_verified"] is True
    blocked = client.post(f"/api/documents/{uploaded.json()['id']}/analyze?force=true")
    assert blocked.status_code == 409
