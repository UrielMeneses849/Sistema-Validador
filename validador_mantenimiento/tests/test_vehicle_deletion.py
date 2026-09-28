from __future__ import annotations

from datetime import date
from itertools import count

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis
from app.models.maintenance import Maintenance
from app.models.service_event import ServiceEvent
from app.models.validation import Validation
from app.models.vehicle import Vehicle


_sequence = count(1)
def create_vehicle(client: TestClient, *, internal_number: str | None = None) -> dict:
    suffix = next(_sequence)
    response = client.post(
        "/api/vehicles",
        json={
            "internal_number": internal_number or f"DELETE-{suffix}",
            "plate": f"DEL-{suffix}",
            "brand": "Ford",
            "model": "Transit",
            "year": 2025,
            "current_odometer": 0,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_document(db: Session, vehicle_id: int) -> Document:
    suffix = next(_sequence)
    document = Document(
        vehicle_id=vehicle_id,
        original_filename=f"history-{suffix}.pdf",
        stored_filename=f"history-{suffix}.pdf",
        file_path=f"/private/tmp/history-{suffix}.pdf",
        mime_type="application/pdf",
        file_size=100,
    )
    db.add(document)
    db.flush()
    return document


def add_related_history(db: Session, vehicle_id: int, relation: str) -> None:
    if relation == "maintenance":
        db.add(
            Maintenance(
                vehicle_id=vehicle_id,
                maintenance_date=date(2026, 1, 1),
                odometer=10_000,
                maintenance_type="Preventivo",
            )
        )
    elif relation == "document":
        document = create_document(db, vehicle_id)
        db.add(
            DocumentAnalysis(
                document_id=document.id,
                extraction_method="pdf_text",
            )
        )
    elif relation == "service_event":
        document = create_document(db, vehicle_id)
        db.add(
            ServiceEvent(
                document_id=document.id,
                vehicle_id=vehicle_id,
                extraction_method="pdf_text",
                service_date=date(2026, 1, 1),
                mileage_km=10_000,
                service_category="preventive_maintenance",
                resets_maintenance_interval=True,
            )
        )
    elif relation == "validation":
        document = create_document(db, vehicle_id)
        validation = Validation(
            validation_code=f"DELETE-VAL-{next(_sequence)}",
            vehicle_id=vehicle_id,
            document_id=document.id,
            status="COMPLIANT",
            message="Validación histórica",
        )
        db.add(validation)
        db.flush()
        db.add(
            AuditLog(
                validation_id=validation.id,
                event_type="FINAL_RESULT",
                message="Resultado histórico",
            )
        )
    else:  # pragma: no cover - protege el helper de pruebas.
        raise AssertionError(f"Relación no soportada: {relation}")
    db.commit()


def test_delete_vehicle_without_relations_removes_it_from_list(client: TestClient):
    vehicle = create_vehicle(client)

    response = client.delete(f"/api/vehicles/{vehicle['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert all(
        item["id"] != vehicle["id"] for item in client.get("/api/vehicles").json()
    )


def test_delete_missing_vehicle_returns_404(client: TestClient):
    response = client.delete("/api/vehicles/999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "No existe el vehículo con ID 999999."


@pytest.mark.parametrize(
    "relation", ["maintenance", "document", "service_event", "validation"]
)
def test_delete_vehicle_with_related_history_removes_the_complete_graph(
    client: TestClient, relation: str
):
    from app.database.database import SessionLocal

    vehicle = create_vehicle(client)
    with SessionLocal() as db:
        add_related_history(db, vehicle["id"], relation)

    response = client.delete(f"/api/vehicles/{vehicle['id']}")

    assert response.status_code == 204
    assert client.get(f"/api/vehicles/{vehicle['id']}").status_code == 404
    with SessionLocal() as db:
        assert db.query(Vehicle).count() == 0
        assert db.query(Maintenance).count() == 0
        assert db.query(Document).count() == 0
        assert db.query(DocumentAnalysis).count() == 0
        assert db.query(ServiceEvent).count() == 0
        assert db.query(Validation).count() == 0
        assert db.query(AuditLog).count() == 0


def test_delete_vehicle_removes_documents_and_evidence_files(
    client: TestClient, tmp_path, monkeypatch
):
    from app.database.database import SessionLocal
    from app.services import training_dataset_service

    evidence_root = tmp_path / "evidence"
    evidence_path = evidence_root / "vehicle" / "date.png"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_bytes(b"crop")
    monkeypatch.setattr(training_dataset_service, "EVIDENCE_CROP_DIR", evidence_root)

    vehicle = create_vehicle(client)
    document_path = tmp_path / "original.pdf"
    document_path.write_bytes(b"%PDF-1.4 test")
    with SessionLocal() as db:
        maintenance = Maintenance(
            vehicle_id=vehicle["id"],
            maintenance_date=date(2026, 1, 1),
            odometer=10_000,
            maintenance_type="Preventivo",
        )
        document = Document(
            vehicle_id=vehicle["id"],
            original_filename="original.pdf",
            stored_filename=f"delete-files-{next(_sequence)}.pdf",
            file_path=str(document_path),
            mime_type="application/pdf",
            file_size=document_path.stat().st_size,
        )
        db.add_all([maintenance, document])
        db.flush()
        db.add(
            DocumentAnalysis(
                document_id=document.id,
                extraction_method="pdf_text",
            )
        )
        event = ServiceEvent(
            document_id=document.id,
            vehicle_id=vehicle["id"],
            extraction_method="pdf_text",
            service_date=date(2026, 1, 1),
            mileage_km=10_000,
            service_category="preventive_maintenance",
            resets_maintenance_interval=True,
            field_evidence={"date": {"crop_id": "vehicle/date.png"}},
        )
        validation = Validation(
            validation_code=f"DELETE-ALL-{next(_sequence)}",
            vehicle_id=vehicle["id"],
            document_id=document.id,
            maintenance_id=maintenance.id,
            status="COMPLIANT",
            message="Validación de prueba",
        )
        db.add_all([event, validation])
        db.flush()
        db.add(
            AuditLog(
                validation_id=validation.id,
                event_type="FINAL_RESULT",
                message="Resultado de prueba",
            )
        )
        db.commit()

    response = client.delete(f"/api/vehicles/{vehicle['id']}")

    assert response.status_code == 204
    assert document_path.exists() is False
    assert evidence_path.exists() is False
    with SessionLocal() as db:
        assert db.query(Vehicle).count() == 0
        assert db.query(Maintenance).count() == 0
        assert db.query(Document).count() == 0
        assert db.query(DocumentAnalysis).count() == 0
        assert db.query(ServiceEvent).count() == 0
        assert db.query(Validation).count() == 0
        assert db.query(AuditLog).count() == 0


def test_legacy_vehicle_without_relations_can_be_deleted_by_id(client: TestClient):
    legacy = create_vehicle(client, internal_number="LEGACY-WITHOUT-CONTRACT")
    assert legacy["numero_contrato"] is None

    response = client.delete(f"/api/vehicles/{legacy['id']}")

    assert response.status_code == 204
    assert client.get(f"/api/vehicles/{legacy['id']}").status_code == 404


def test_vehicles_frontend_requires_confirmation_and_exposes_delete_feedback(
    client: TestClient,
):
    page = client.get("/vehicles.html")
    javascript = client.get("/js/vehicles.js")
    stylesheet = client.get("/css/styles.css")

    assert page.status_code == 200
    assert 'id="vehicle-delete-dialog"' in page.text
    assert "¿Eliminar este vehículo?" in page.text
    assert "sus mantenimientos, documentos y validaciones asociados" in page.text
    assert "No se puede deshacer" in page.text
    assert 'id="delete-vehicle-contract"' in page.text
    assert 'id="delete-vehicle-brand"' in page.text
    assert 'id="delete-vehicle-model"' in page.text
    assert 'id="vehicle-delete-error"' in page.text
    assert ">Cancelar<" in page.text
    assert ">Eliminar vehículo<" in page.text
    assert 'data-action="delete"' in javascript.text
    assert "deleteDialog.showModal()" in javascript.text
    assert "Vehículo y datos asociados eliminados correctamente." in javascript.text
    assert "deleteError.textContent = error.message" in javascript.text
    assert 'deleteError.className = "notice error show"' in javascript.text
    assert ".confirmation-dialog" in stylesheet.text
    assert ".button-danger-solid" in stylesheet.text


def test_cancel_confirmation_has_no_delete_request(client: TestClient):
    javascript = client.get("/js/vehicles.js").text
    cancel_handler = javascript.split(
        'deleteCancel.addEventListener("click", closeDeleteDialog)'
    )[1].split('deleteDialog.addEventListener("cancel"')[0]
    open_handler = javascript.split("function requestVehicleDeletion")[1].split(
        'vehicleForm.addEventListener("submit"'
    )[0]

    assert "API.remove" not in cancel_handler
    assert "API.remove" not in open_handler
    assert 'deleteForm.addEventListener("submit"' in javascript
    assert "await API.remove(`/vehicles/${vehicleId}`)" in javascript
