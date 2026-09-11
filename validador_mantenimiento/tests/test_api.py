from fastapi.testclient import TestClient


def test_frontend_routes_keep_audit_and_dashboard_available(client: TestClient):
    audit = client.get("/")
    dashboard = client.get("/dashboard.html")

    assert audit.status_code == 200
    assert "Verifica el historial" in audit.text
    assert "/js/audit.js" in audit.text
    assert dashboard.status_code == 200
    assert "Últimas validaciones" in dashboard.text


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


def test_contract_vehicle_uses_server_consecutive_and_is_listed_for_validation(client: TestClient):
    # Un identificador heredado numérico no debe reutilizarse como contrato.
    legacy = client.post(
        "/api/vehicles",
        json={
            "internal_number": "835414",
            "plate": "LEG-001",
            "brand": "Ford",
            "model": "Transit",
            "year": 2024,
            "current_odometer": 0,
        },
    )
    assert legacy.status_code == 201
    assert client.get("/api/vehicles/next-contract").json() == {"numero_contrato": "835415"}

    created = client.post(
        "/api/vehicles",
        json={
            # La vista puede enviar su previsualización, pero el servidor es la
            # fuente de verdad y debe asignar el consecutivo disponible.
            "numero_contrato": "111111",
            "brand": "Toyota",
            "model": "Hiace",
            "kilometraje": 42,
            "fecha_factura_origen": "2025-01-15",
            "fecha_inicio_contrato": "2026-01-01",
            "fecha_fin_contrato": "2027-01-01",
        },
    )
    assert created.status_code == 201, created.text
    vehicle = created.json()
    assert vehicle["numero_contrato"] == "835415"
    assert vehicle["kilometraje"] == vehicle["current_odometer"] == 42

    active_vehicles = client.get("/api/vehicles?status=active")
    assert active_vehicles.status_code == 200
    assert any(item["id"] == vehicle["id"] for item in active_vehicles.json())
    assert client.get("/api/vehicles/next-contract").json() == {"numero_contrato": "835416"}


def test_contract_number_is_immutable_and_contract_dates_remain_valid(client: TestClient):
    created = client.post(
        "/api/vehicles",
        json={
            "brand": "Nissan",
            "model": "NP300",
            "kilometraje": 0,
            "fecha_factura_origen": "2025-01-01",
            "fecha_inicio_contrato": "2026-03-01",
            "fecha_fin_contrato": "2027-03-01",
        },
    )
    assert created.status_code == 201
    vehicle_id = created.json()["id"]

    changed_contract = client.put(f"/api/vehicles/{vehicle_id}", json={"numero_contrato": "999999"})
    assert changed_contract.status_code == 422
    assert "no se puede modificar" in changed_contract.json()["detail"]

    invalid_range = client.put(f"/api/vehicles/{vehicle_id}", json={"fecha_fin_contrato": "2026-01-01"})
    assert invalid_range.status_code == 422
    assert "no puede ser anterior" in invalid_range.json()["detail"]


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
