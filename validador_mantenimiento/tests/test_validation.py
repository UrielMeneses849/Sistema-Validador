from datetime import date

import pytest
from fastapi.testclient import TestClient


def create_reference(client: TestClient) -> tuple[int, int]:
    vehicle = client.post(
        "/api/vehicles",
        json={
            "internal_number": "VEH-001",
            "plate": "ABC-123-A",
            "vin": "VIN-001",
            "brand": "Ford",
            "model": "Transit",
            "year": 2024,
            "current_odometer": 40_000,
            "status": "active",
            "vehicle_condition": "used",
            "initial_odometer": 40_000,
        },
    )
    assert vehicle.status_code == 201
    vehicle_id = vehicle.json()["id"]
    maintenance = client.post(
        f"/api/vehicles/{vehicle_id}/maintenances",
        json={
            "maintenance_date": "2026-02-15",
            "odometer": 40_000,
            "maintenance_type": "Preventivo",
            "description": "Mantenimiento de referencia",
        },
    )
    assert maintenance.status_code == 201
    return vehicle_id, maintenance.json()["id"]


def upload_pdf(client: TestClient, vehicle_id: int) -> int:
    response = client.post(
        "/api/documents/upload",
        data={"vehicle_id": str(vehicle_id)},
        files={"file": ("factura.pdf", b"%PDF-1.4 documento de prueba", "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.parametrize(
    ("document_date", "document_odometer", "expected_status", "uses_maintenance"),
    [
        ("2026-05-01", 50_000, "COMPLIANT", True),
        ("2026-08-15", 45_000, "COMPLIANT", True),
        ("2026-05-01", 45_000, "COMPLIANT", True),
        ("2026-08-20", 39_000, "REQUIRES_REVIEW", True),
        ("2026-01-01", 50_000, "REQUIRES_REVIEW", False),
    ],
)
def test_required_validation_cases(
    client: TestClient,
    document_date: str,
    document_odometer: int,
    expected_status: str,
    uses_maintenance: bool,
):
    vehicle_id, maintenance_id = create_reference(client)
    document_id = upload_pdf(client, vehicle_id)
    response = client.post(
        "/api/validations",
        json={
            "vehicle_id": vehicle_id,
            "document_id": document_id,
            "document_date": document_date,
            "document_odometer": document_odometer,
            "maintenance_type": "Preventivo",
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == expected_status
    assert result["maintenance_id"] == (maintenance_id if uses_maintenance else None)
    assert result["validation_code"].startswith("VAL-")
    assert len(result["audit_logs"]) >= 4


def test_missing_maintenance_is_review_required(client: TestClient):
    vehicle = client.post(
        "/api/vehicles",
        json={
            "internal_number": "VEH-SIN-REF",
            "plate": "REF-000",
            "brand": "Nissan",
            "model": "NP300",
            "year": 2024,
            "current_odometer": 0,
        },
    ).json()
    document_id = upload_pdf(client, vehicle["id"])
    result = client.post(
        "/api/validations",
        json={"vehicle_id": vehicle["id"], "document_id": document_id, "document_date": "2026-08-20", "document_odometer": 100},
    )
    assert result.status_code == 201
    assert result.json()["status"] == "REQUIRES_REVIEW"
