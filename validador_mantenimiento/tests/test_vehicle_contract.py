from fastapi.testclient import TestClient


def contract_payload(**overrides):
    payload = {
        "brand": "Toyota",
        "model": "Sienna",
        "kilometraje": 12_345,
        "fecha_factura_origen": "2026-01-10",
        "fecha_inicio_contrato": "2026-02-01",
        "fecha_fin_contrato": "2027-02-01",
    }
    payload.update(overrides)
    return payload


def test_contract_vehicle_is_persisted_and_available_to_the_validator(client: TestClient):
    preview = client.get("/api/vehicles/next-contract")
    assert preview.status_code == 200
    assert preview.json()["numero_contrato"] == "835414"

    created = client.post("/api/vehicles", json=contract_payload())
    assert created.status_code == 201, created.text
    vehicle = created.json()
    assert vehicle["numero_contrato"] == "835414"
    assert vehicle["brand"] == "Toyota"
    assert vehicle["model"] == "Sienna"
    assert vehicle["kilometraje"] == 12_345
    assert vehicle["plate"] == "CONTRATO-835414"
    assert vehicle["year"] == 2026

    # Esta es la misma consulta que usa UI.vehicleOptions en Validar documento.
    selectable = client.get("/api/vehicles?status=active")
    assert selectable.status_code == 200
    assert any(item["id"] == vehicle["id"] for item in selectable.json())

    next_preview = client.get("/api/vehicles/next-contract")
    assert next_preview.status_code == 200
    assert next_preview.json()["numero_contrato"] == "835415"


def test_contract_vehicle_rejects_missing_or_invalid_contract_data(client: TestClient):
    missing_date = client.post(
        "/api/vehicles",
        json=contract_payload(fecha_fin_contrato=None),
    )
    assert missing_date.status_code == 422

    invalid_mileage = client.post(
        "/api/vehicles",
        json=contract_payload(kilometraje=-1),
    )
    assert invalid_mileage.status_code == 422

    invalid_range = client.post(
        "/api/vehicles",
        json=contract_payload(fecha_fin_contrato="2026-01-31"),
    )
    assert invalid_range.status_code == 422
