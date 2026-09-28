from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import STORAGE_DIR
from app.database.database import SessionLocal
from app.models.document import Document
from app.services.contract_extraction_service import parse_contract_text
from tests.pdf_fixture import text_pdf


CONTRACT_TEXT = """GARANTIPLUS MÉXICO
DATOS GENERALES
Nº CONTRATO 401266
Fecha de Contrato 05/04/2025
Producto Contratado EXCELLENCE SUV GM M2 24 Limite por Avería Valor Venta Vehículo
MARCA CHEVROLET SUV NUMERO DE SERIE (VIN) 93CEC76C2PB103898
MODELO TRACKER FECHA 1º FACTURA 27/06/2022 KILOMETROS 36000
PERIODO DE VIGENCIA DEL CONTRATO
FECHA INICIO GARANTIA 05/04/2025 FECHA FIN GARANTIA 04/04/2027
"""


def test_contract_parser_extracts_required_fields_and_tolerates_ocr_variants():
    parsed = parse_contract_text(
        "NUMERO DE CONTRATO: 735985 PRODUCTO CONTRATADO EXCELLENCE M2 LIMITE POR AVERIA "
        "MARCA CHEVROLET NUMERO DE SERIE LZWMLMGM3NG023664 "
        "MODELO GROOVE FECHA 1o FACTURA: 27-04-2022 Kms. 64,670 "
        "FECHA DE INICIO DE CONTRATO 22-04-2026 FECHA DE FIN DE CONTRATO 21-04-2027",
        extraction_method="ocr",
    )

    assert parsed.numero_contrato == "735985"
    assert parsed.fecha_factura_origen == date(2022, 4, 27)
    assert parsed.fecha_inicio_contrato == date(2026, 4, 22)
    assert parsed.fecha_fin_contrato == date(2027, 4, 21)
    assert parsed.initial_odometer == 64_670
    assert parsed.brand == "CHEVROLET"
    assert parsed.model == "GROOVE"
    assert parsed.vehicle_condition == "used"
    assert parsed.extraction_method == "ocr"


def test_contract_parser_handles_overlapping_brand_and_date_columns():
    parsed = parse_contract_text(
        "Nº CONTRATO 161257 Fecha de Contrato 20/05/2024 "
        "Producto Contratado EXCELLENCE TT GM M1 12 Limite por Avería "
        "MARCA CHEVROLET TT GMN UMERO DE SERIE (VIN) 3GCPD9EK3RG261519 "
        "MODELO SILVERADO 4X4 (F -F KE)C HA 1º FACTURA 24/04/2024 KILOMETROS 100 "
        "FECHA INICIO GARANTIA 26/04/2025 FECHA FIN GARANTIA 25/04/2026"
    )

    assert parsed.numero_contrato == "161257"
    assert parsed.brand == "CHEVROLET"
    assert parsed.model == "SILVERADO 4X4"
    assert parsed.vehicle_condition == "new"
    assert parsed.initial_odometer == 100
    assert parsed.fecha_factura_origen == date(2024, 4, 24)
    assert parsed.fecha_inicio_contrato == date(2025, 4, 26)
    assert parsed.fecha_fin_contrato == date(2026, 4, 25)


def test_extract_contract_endpoint_does_not_persist_pdf_or_document(client: TestClient, tmp_path):
    pdf = text_pdf(tmp_path / "401266.pdf", CONTRACT_TEXT)
    storage_before = {path.name for path in STORAGE_DIR.iterdir()}

    response = client.post(
        "/api/vehicles/extract-contract",
        files={"file": ("401266.pdf", pdf, "application/pdf")},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "numero_contrato": "401266",
        "fecha_factura_origen": "2022-06-27",
        "fecha_inicio_contrato": "2025-04-05",
        "fecha_fin_contrato": "2027-04-04",
        "initial_odometer": 36_000,
        "brand": "CHEVROLET",
        "model": "TRACKER",
        "vehicle_condition": "used",
        "extraction_method": "pdf_text",
    }
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Document)) == 0
    assert {path.name for path in STORAGE_DIR.iterdir()} == storage_before


def test_extracted_contract_number_is_preserved_and_cannot_be_duplicated(client: TestClient):
    payload = {
        "numero_contrato": "401266",
        "brand": "Chevrolet",
        "model": "Tracker",
        "kilometraje": 36_000,
        "fecha_factura_origen": "2022-06-27",
        "fecha_inicio_contrato": "2025-04-05",
        "fecha_fin_contrato": "2027-04-04",
    }

    created = client.post("/api/vehicles", json=payload)
    duplicate = client.post("/api/vehicles", json=payload)

    assert created.status_code == 201, created.text
    assert created.json()["numero_contrato"] == "401266"
    assert created.json()["internal_number"] == "401266"
    assert duplicate.status_code == 409
    assert "ya está registrado" in duplicate.json()["detail"]
    assert client.get("/api/vehicles/next-contract").json() == {"numero_contrato": "835414"}


def test_contract_upload_rejects_non_pdf_and_incomplete_contract(client: TestClient, tmp_path):
    invalid = client.post(
        "/api/vehicles/extract-contract",
        files={"file": ("contrato.pdf", b"no es pdf", "application/pdf")},
    )
    incomplete_pdf = text_pdf(tmp_path / "incomplete.pdf", "Nº CONTRATO 401266")
    incomplete = client.post(
        "/api/vehicles/extract-contract",
        files={"file": ("incomplete.pdf", incomplete_pdf, "application/pdf")},
    )

    assert invalid.status_code == 400
    assert incomplete.status_code == 422
    assert "fecha" in incomplete.json()["detail"].lower()


def test_vehicle_form_exposes_contract_pdf_autofill(client: TestClient):
    page = client.get("/vehicles.html")
    javascript = client.get("/js/vehicles.js")
    api_javascript = client.get("/js/api.js")

    assert 'id="contract-pdf"' in page.text
    assert 'accept="application/pdf,.pdf"' in page.text
    assert "sólo tendrás que capturar el kilometraje actual" in page.text
    assert "El PDF no se guarda" in page.text
    assert "API.extractContract(file)" in javascript.text
    assert "result.fecha_factura_origen" in javascript.text
    assert "result.fecha_inicio_contrato" in javascript.text
    assert "result.fecha_fin_contrato" in javascript.text
    assert "result?.initial_odometer" in javascript.text
    assert "result?.vehicle_condition" in javascript.text
    assert "result?.brand" in javascript.text
    assert "result?.model" in javascript.text
    extraction_handler = javascript.text.split("async function extractContractFromPdf")[1].split(
        "function isImportableLegacyVehicle"
    )[0]
    assert "elements.initial_odometer.value" in extraction_handler
    assert "elements.kilometraje.value" not in extraction_handler
    assert 'vehiclePayload(values, { includeContract: extractedContractNumber === values.numero_contrato })' in javascript.text
    assert 'this.request("/vehicles/extract-contract"' in api_javascript.text
