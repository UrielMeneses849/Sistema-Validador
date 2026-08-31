from fastapi.testclient import TestClient


def test_vehicle_crud_and_logical_deactivation(client: TestClient):
    created = client.post(
        "/api/vehicles",
        json={
            "internal_number": "VEH-API-01",
            "plate": "API-001",
            "brand": "Toyota",
            "model": "Hiace",
            "year": 2025,
            "current_odometer": 20,
        },
    )
    assert created.status_code == 201
    vehicle_id = created.json()["id"]
    updated = client.put(f"/api/vehicles/{vehicle_id}", json={"current_odometer": 30})
    assert updated.status_code == 200
    assert updated.json()["current_odometer"] == 30
    deactivated = client.delete(f"/api/vehicles/{vehicle_id}")
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "inactive"
    assert client.get(f"/api/vehicles/{vehicle_id}").status_code == 200


def test_document_rejects_empty_or_unsupported_file(client: TestClient):
    vehicle = client.post(
        "/api/vehicles",
        json={
            "internal_number": "VEH-FILE-01",
            "plate": "FILE-01",
            "brand": "Kia",
            "model": "Bongo",
            "year": 2024,
            "current_odometer": 0,
        },
    ).json()
    response = client.post(
        "/api/documents/upload",
        data={"vehicle_id": str(vehicle["id"])},
        files={"file": ("vacio.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 422
    assert "vacío" in response.json()["detail"]

