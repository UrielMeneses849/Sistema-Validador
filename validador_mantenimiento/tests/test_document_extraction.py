from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.document_parser import parse_document
from app.services.extraction_service import HybridExtractor
from tests.pdf_fixture import REFERENCE_12000_TEXT, REFERENCE_TEXT, text_pdf


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


def test_individual_service_uses_labeled_odometer_not_commercial_interval(tmp_path):
    path = tmp_path / "service-12000.pdf"
    text_pdf(path, REFERENCE_12000_TEXT)

    parsed = parse_document(HybridExtractor().extract(str(path), "application/pdf"))
    event = parsed.service_events[0]

    assert event.service_date.normalized_value.isoformat() == "2024-07-01"
    assert event.mileage.normalized_value == 12047
    assert event.mileage.normalized_value != 12000
    assert event.resets_maintenance_interval is True


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


def test_force_reanalysis_replaces_only_unconfirmed_automatic_results(client: TestClient, tmp_path):
    vehicle_id = create_vehicle(client, "VEH-REPROCESS-01")
    pdf = text_pdf(tmp_path / "servicio.pdf", REFERENCE_TEXT)
    uploaded = client.post(
        "/api/documents/upload",
        data={"vehicle_id": str(vehicle_id)},
        files={"file": ("servicio.pdf", pdf, "application/pdf")},
    )
    document_id = uploaded.json()["id"]
    first = client.post(f"/api/documents/{document_id}/analyze")
    assert first.status_code == 200
    first_event = first.json()["service_events"][0]

    # Una validación automática anterior se invalida al reanalizar el mismo original.
    assert client.post(f"/api/service-events/{first_event['id']}/validate").status_code == 200
    refreshed = client.post(f"/api/documents/{document_id}/analyze?force=true")
    assert refreshed.status_code == 200, refreshed.text
    refreshed_event = refreshed.json()["service_events"][0]
    assert refreshed_event["mileage_km"] == 6399
    assert refreshed_event["user_confirmed"] is False

    # Una corrección humana sí queda protegida contra un reemplazo automático.
    assert client.put(f"/api/service-events/{refreshed_event['id']}", json={"mileage_km": 6400}).status_code == 200
    blocked = client.post(f"/api/documents/{document_id}/analyze?force=true")
    assert blocked.status_code == 409
    assert "confirmado" in blocked.json()["detail"]


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
