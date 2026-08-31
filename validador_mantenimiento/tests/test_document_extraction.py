from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.document_parser import parse_document
from app.services.extraction_service import HybridExtractor
from tests.pdf_fixture import REFERENCE_TEXT, text_pdf


def create_vehicle(client: TestClient, number: str = "VEH-AUTO-01") -> int:
    response = client.post(
        "/api/vehicles",
        json={
            "internal_number": number,
            "plate": "WDN046B",
            "vin": "LZWLLNGL0RB008731",
            "brand": "Chevrolet",
            "model": "Captiva",
            "year": 2024,
            "current_odometer": 0,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_pdf_text_extractor_and_parser_use_digital_text(tmp_path):
    path = tmp_path / "service.pdf"
    text_pdf(path, REFERENCE_TEXT)

    extraction = HybridExtractor().extract(str(path), "application/pdf")
    parsed = parse_document(extraction)

    assert extraction.method == "pdf_text"
    assert extraction.has_usable_text is True
    assert "Kilometraje: 6399" in extraction.text
    assert parsed.document_type == "comprobante_servicio"
    assert parsed.fields["vin"].normalized_value == "LZWLLNGL0RB008731"
    assert parsed.fields["plates"].normalized_value == "WDN046B"
    assert parsed.fields["model"].normalized_value == "CAPTIVA"
    assert parsed.service_events[0].service_date.normalized_value.isoformat() == "2024-03-23"
    assert parsed.service_events[0].mileage.normalized_value == 6399
    assert parsed.service_events[0].mileage.normalized_value != 6000
    assert parsed.service_events[0].resets_maintenance_interval is True


def test_document_api_analyzes_then_marks_first_maintenance_available(client: TestClient, tmp_path):
    vehicle_id = create_vehicle(client)
    pdf = text_pdf(tmp_path / "servicio.pdf", REFERENCE_TEXT)
    uploaded = client.post(
        "/api/documents/upload",
        data={"vehicle_id": str(vehicle_id)},
        files={"file": ("servicio.pdf", pdf, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text

    analysis = client.post(f"/api/documents/{uploaded.json()['id']}/analyze")
    assert analysis.status_code == 200, analysis.text
    payload = analysis.json()
    assert payload["extraction_method"] == "pdf_text"
    assert payload["service_events"][0]["mileage_km"] == 6399
    assert payload["service_events"][0]["resets_maintenance_interval"] is True

    validation = client.post(f"/api/service-events/{payload['service_events'][0]['id']}/validate")
    assert validation.status_code == 200, validation.text
    assert validation.json()["status"] == "FIRST_MAINTENANCE_AVAILABLE"
    assert validation.json()["analysis_details"]["interval_validation"] == "not_applicable"


def test_manual_fallback_uses_the_same_first_maintenance_rule(client: TestClient):
    vehicle_id = create_vehicle(client, "VEH-MANUAL-01")
    uploaded = client.post(
        "/api/documents/upload",
        data={"vehicle_id": str(vehicle_id)},
        files={"file": ("escaneado.pdf", b"%PDF-1.4 sin texto", "application/pdf")},
    )
    assert uploaded.status_code == 201

    event = client.post(
        "/api/service-events",
        json={
            "document_id": uploaded.json()["id"],
            "service_date": "2024-03-23",
            "mileage_km": 6399,
            "service_type": "Mantenimiento preventivo",
            "description": "Cambio de aceite y filtro",
        },
    )
    assert event.status_code == 201, event.text
    result = client.post(f"/api/service-events/{event.json()['id']}/validate")
    assert result.status_code == 200
    assert result.json()["status"] == "FIRST_MAINTENANCE_AVAILABLE"
