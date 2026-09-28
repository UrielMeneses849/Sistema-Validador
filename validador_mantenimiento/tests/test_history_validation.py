from __future__ import annotations

from datetime import date
from itertools import count

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import STORAGE_DIR
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis
from app.models.manufacturer_maintenance_rule import ManufacturerMaintenanceRule
from app.models.service_event import ServiceEvent
from app.models.validation import Validation
from app.models.vehicle import Vehicle
from tests.pdf_fixture import REFERENCE_TEXT, text_pdf


_sequence = count(1)


def create_vehicle(
    db: Session,
    *,
    condition: str = "used",
    brand: str = "Ford",
    initial: int = 40_000,
) -> Vehicle:
    suffix = next(_sequence)
    vehicle = Vehicle(
        internal_number=f"HISTORY-{suffix}",
        plate=f"H{suffix:06d}",
        brand=brand,
        model="Historial",
        year=2026,
        current_odometer=initial,
        vehicle_condition=condition,
        initial_odometer=initial,
        fecha_inicio_contrato=date(2026, 1, 1),
    )
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


def create_event(
    db: Session,
    vehicle: Vehicle,
    *,
    when: date,
    mileage: int,
    filename: str,
    requires_review: bool = False,
    confidence: str = "high",
) -> ServiceEvent:
    suffix = next(_sequence)
    document = Document(
        vehicle_id=vehicle.id,
        original_filename=filename,
        stored_filename=f"history-{suffix}.pdf",
        file_path=f"/private/tmp/history-{suffix}.pdf",
        mime_type="application/pdf",
        file_size=100,
    )
    db.add(document)
    db.flush()
    analysis = DocumentAnalysis(
        document_id=document.id,
        extraction_method="pdf_text",
        extraction_status="completed",
        extracted_text=f"Servicio {when.isoformat()} a {mileage} km",
        document_type="comprobante_servicio",
        confidence=confidence,
        requires_human_review=requires_review,
        warnings=["Confirmar fecha y kilometraje."] if requires_review else [],
        extracted_fields={
            "vin": {"normalized_value": "VIN-HISTORY-001"},
            "plates": {"normalized_value": vehicle.plate},
            "invoice_number": {"normalized_value": f"FAC-{suffix}"},
        },
    )
    db.add(analysis)
    db.flush()
    event = ServiceEvent(
        document_id=document.id,
        vehicle_id=vehicle.id,
        extraction_method="pdf_text",
        service_date=when,
        service_date_raw=when.isoformat(),
        mileage_km=mileage,
        mileage_raw=str(mileage),
        repair_order_number=f"RO-{suffix}",
        dealer="Taller de prueba",
        service_category="preventive_maintenance",
        service_type="Mantenimiento preventivo",
        description="Cambio de aceite y filtro",
        resets_maintenance_interval=True,
        confidence=confidence,
        requires_human_review=requires_review,
        field_evidence={"date": {"confidence": confidence}},
        warnings=["Confirmar fecha y kilometraje."] if requires_review else [],
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def validate_history(client: TestClient, vehicle: Vehicle, events: list[ServiceEvent]):
    response = client.post(
        "/api/history-validations",
        json={"vehicle_id": vehicle.id, "event_ids": [event.id for event in events]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_batch_orders_three_documents_and_builds_one_timeline(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db, initial=0)
        first = create_event(
            db, vehicle, when=date(2026, 3, 23), mileage=6_399, filename="servicio_6000.pdf"
        )
        second = create_event(
            db, vehicle, when=date(2026, 8, 20), mileage=11_800, filename="servicio_12000.pdf"
        )
        third = create_event(
            db, vehicle, when=date(2027, 1, 5), mileage=18_000, filename="servicio_18000.pdf"
        )

        history = validate_history(client, vehicle, [third, first, second])

        assert [row["service_event"]["id"] for row in history["rows"]] == [
            first.id,
            second.id,
            third.id,
        ]
        assert history["baseline"] == {
            "source": "contract_start",
            "date": "2026-01-01",
            "mileage_km": 0,
        }
        assert history["rows"][0]["validation"]["analysis_details"]["baseline"]["source"] == "contract_start"
        assert history["rows"][1]["validation"]["analysis_details"]["baseline"]["source_id"] == first.id
        assert history["rows"][1]["elapsed_time"] == "4 meses, 28 días"
        assert history["rows"][1]["delta_km"] == 5_401
        assert history["summary"]["services_analyzed"] == 3


def test_silverado_sequence_uses_previous_visible_service_for_11414_km(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        db.add(
            ManufacturerMaintenanceRule(
                brand="CHEVROLET",
                interval_months=12,
                interval_km=12_000,
                active=True,
            )
        )
        db.commit()
        vehicle = create_vehicle(db, condition="new", brand="CHEVROLET", initial=100)
        vehicle.fecha_inicio_contrato = date(2024, 4, 26)
        db.commit()
        events = [
            create_event(db, vehicle, when=date(2024, 7, 3), mileage=11_695, filename="03-07-2024.pdf"),
            create_event(db, vehicle, when=date(2024, 9, 4), mileage=23_019, filename="04-09-2024.pdf"),
            create_event(db, vehicle, when=date(2024, 11, 12), mileage=34_731, filename="12-11-2024.pdf"),
            create_event(db, vehicle, when=date(2025, 1, 10), mileage=46_145, filename="10-01-2025.pdf"),
        ]

        history = validate_history(client, vehicle, list(reversed(events)))
        january = history["rows"][3]

        assert [row["validation"]["status"] for row in history["rows"]] == [
            "COMPLIANT",
            "COMPLIANT",
            "COMPLIANT",
            "COMPLIANT",
        ]
        assert january["delta_km"] == 11_414
        assert january["validation"]["analysis_details"]["delta_km"] == 11_414
        assert january["validation"]["analysis_details"]["baseline"]["source_id"] == events[2].id


def test_batch_includes_previously_persisted_events_for_the_same_vehicle(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        existing = create_event(
            db, vehicle, when=date(2026, 2, 1), mileage=42_000, filename="existing.pdf"
        )
        newly_selected = create_event(
            db, vehicle, when=date(2026, 5, 1), mileage=48_000, filename="new.pdf"
        )

        history = validate_history(client, vehicle, [newly_selected])

        assert [row["service_event"]["id"] for row in history["rows"]] == [
            existing.id,
            newly_selected.id,
        ]
        assert history["rows"][1]["validation"]["analysis_details"]["baseline"]["source_id"] == existing.id


def test_preview_can_repeat_same_pdf_without_saving_or_mixing_previous_events(
    client: TestClient, tmp_path
):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db, initial=0)
        vehicle.fecha_inicio_contrato = date(2024, 1, 1)
        persisted = create_event(
            db,
            vehicle,
            when=date(2024, 2, 1),
            mileage=1_000,
            filename="previously-saved.pdf",
        )
        counts_before = {
            "documents": db.query(Document).count(),
            "analyses": db.query(DocumentAnalysis).count(),
            "events": db.query(ServiceEvent).count(),
            "validations": db.query(Validation).count(),
        }
        stored_files_before = {path.name for path in STORAGE_DIR.iterdir()}
        pdf = text_pdf(tmp_path / "repeatable.pdf", REFERENCE_TEXT)

        for _ in range(2):
            response = client.post(
                "/api/history-validations/preview",
                data={"vehicle_id": str(vehicle.id)},
                files=[("files", ("repeatable.pdf", pdf, "application/pdf"))],
            )

            assert response.status_code == 200, response.text
            preview = response.json()
            assert preview["ephemeral"] is True
            assert len(preview["preview_document_ids"]) == 1
            assert len(preview["rows"]) == 1
            assert preview["rows"][0]["document"]["id"] == preview["preview_document_ids"][0]
            assert preview["rows"][0]["service_event"]["id"] != persisted.id
            assert preview["rows"][0]["service_event"]["mileage_km"] == 6_399
            assert preview["rows"][0]["delta_km"] == 6_399

            db.expire_all()
            assert {
                "documents": db.query(Document).count(),
                "analyses": db.query(DocumentAnalysis).count(),
                "events": db.query(ServiceEvent).count(),
                "validations": db.query(Validation).count(),
            } == counts_before
            assert {path.name for path in STORAGE_DIR.iterdir()} == stored_files_before


def test_new_history_uses_active_manufacturer_policy(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        db.add(
            ManufacturerMaintenanceRule(
                brand="FORD", interval_months=12, interval_km=6_000, active=True
            )
        )
        db.commit()
        vehicle = create_vehicle(db, condition="new", initial=0)
        event = create_event(
            db, vehicle, when=date(2026, 6, 1), mileage=6_001, filename="m1.pdf"
        )

        history = validate_history(client, vehicle, [event])

        assert history["policy"] == {
            "source": "manufacturer",
            "brand": "FORD",
            "months": 12,
            "kilometers": 6_000,
            "rule_id": history["policy"]["rule_id"],
        }
        assert history["rows"][0]["validation"]["status"] == "TOLERANCE_PERIOD"


def test_used_history_exposes_fixed_policy(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        event = create_event(
            db, vehicle, when=date(2026, 5, 1), mileage=49_000, filename="used.pdf"
        )

        history = validate_history(client, vehicle, [event])

        assert history["policy"]["source"] == "used_vehicle_default"
        assert history["policy"]["months"] == 6
        assert history["policy"]["kilometers"] == 10_000


@pytest.mark.parametrize(
    ("when", "mileage", "expected"),
    [
        (date(2026, 7, 2), 49_000, "TOLERANCE_PERIOD"),
        (date(2026, 5, 1), 50_001, "TOLERANCE_PERIOD"),
        (date(2026, 8, 2), 49_000, "EXCEEDED_TIME"),
        (date(2026, 5, 1), 51_001, "EXCEEDED_MILEAGE"),
        (date(2026, 8, 2), 51_001, "EXCEEDED_BOTH"),
        (date(2026, 7, 1), 50_000, "COMPLIANT"),
    ],
)
def test_consolidated_history_preserves_val002_boundaries(
    client: TestClient, when: date, mileage: int, expected: str
):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        event = create_event(db, vehicle, when=when, mileage=mileage, filename="limit.pdf")
        history = validate_history(client, vehicle, [event])
        assert history["rows"][0]["validation"]["status"] == expected


def test_reviewed_event_keeps_calculations_and_is_used_as_baseline(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        uncertain = create_event(
            db,
            vehicle,
            when=date(2026, 3, 1),
            mileage=45_000,
            filename="uncertain.pdf",
            requires_review=True,
            confidence="low",
        )
        current = create_event(
            db, vehicle, when=date(2026, 6, 1), mileage=49_000, filename="current.pdf"
        )

        history = validate_history(client, vehicle, [current, uncertain])

        assert history["rows"][0]["validation"]["status"] == "REQUIRES_REVIEW"
        assert history["rows"][1]["validation"]["status"] == "COMPLIANT"
        assert history["rows"][0]["delta_km"] == 5_000
        assert history["rows"][1]["validation"]["analysis_details"]["baseline"]["source_id"] == uncertain.id
        assert history["rows"][1]["delta_km"] == 4_000
        assert history["summary"]["requires_review"] == 1


def test_history_deltas_follow_each_visible_row_even_when_events_are_not_maintenance(
    client: TestClient,
):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db, initial=0)
        vehicle.fecha_inicio_contrato = date(2024, 1, 1)
        first = create_event(
            db,
            vehicle,
            when=date(2024, 9, 4),
            mileage=23_019,
            filename="first-review.pdf",
            requires_review=True,
        )
        second = create_event(
            db,
            vehicle,
            when=date(2025, 1, 10),
            mileage=46_145,
            filename="second-review.pdf",
            requires_review=True,
        )
        for event in (first, second):
            event.service_category = "unknown"
            event.resets_maintenance_interval = None
        db.commit()

        history = validate_history(client, vehicle, [second, first])

        assert [row["validation"]["status"] for row in history["rows"]] == [
            "NOT_MAINTENANCE_EVENT",
            "NOT_MAINTENANCE_EVENT",
        ]
        assert history["rows"][0]["elapsed_time"] == "8 meses, 3 días"
        assert history["rows"][0]["delta_km"] == 23_019
        assert history["rows"][1]["elapsed_time"] == "4 meses, 6 días"
        assert history["rows"][1]["delta_km"] == 23_126


def test_human_correction_recalculates_all_later_references(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        uncertain = create_event(
            db,
            vehicle,
            when=date(2026, 3, 1),
            mileage=45_000,
            filename="uncertain.pdf",
            requires_review=True,
            confidence="low",
        )
        later = create_event(
            db, vehicle, when=date(2026, 8, 2), mileage=49_000, filename="later.pdf"
        )

        before = validate_history(client, vehicle, [later, uncertain])
        assert before["rows"][1]["validation"]["status"] == "COMPLIANT"
        assert before["rows"][1]["delta_km"] == 4_000

        corrected = client.put(
            f"/api/service-events/{uncertain.id}",
            json={"service_date": "2026-03-01", "mileage_km": 45_000},
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["requires_human_review"] is False

        after = validate_history(client, vehicle, [later, uncertain])
        assert after["rows"][0]["validation"]["status"] == "COMPLIANT"
        assert after["rows"][1]["validation"]["status"] == "COMPLIANT"
        assert after["rows"][1]["validation"]["analysis_details"]["baseline"]["source_id"] == uncertain.id
        assert after["rows"][1]["delta_km"] == 4_000


def test_pdf_reference_values_keep_elapsed_time_and_kilometer_differences(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db, initial=5_000)
        vehicle.fecha_inicio_contrato = date(2024, 1, 1)
        db.commit()
        first = create_event(
            db,
            vehicle,
            when=date(2024, 12, 12),
            mileage=9_905,
            filename="09_Mazda_S1_10000km_detallado.pdf",
            requires_review=True,
        )
        second = create_event(
            db,
            vehicle,
            when=date(2026, 9, 7),
            mileage=50_640,
            filename="10_Mazda_S5_50000km_detallado.pdf",
            requires_review=True,
        )

        history = validate_history(client, vehicle, [second, first])

        assert [row["service_event"]["id"] for row in history["rows"]] == [first.id, second.id]
        assert history["rows"][0]["delta_km"] == 4_905
        assert history["rows"][0]["elapsed_time"] == "11 meses, 11 días"
        assert history["rows"][1]["delta_km"] == 40_735
        assert history["rows"][1]["elapsed_time"] == "1 año, 8 meses, 26 días"


def test_tolerance_summary_and_analysis_details_expose_all_limits(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        event = create_event(
            db,
            vehicle,
            when=date(2026, 7, 15),
            mileage=50_500,
            filename="tolerance.pdf",
        )

        history = validate_history(client, vehicle, [event])
        validation = history["rows"][0]["validation"]
        details = validation["analysis_details"]

        assert validation["status"] == "TOLERANCE_PERIOD"
        assert validation["validation_state"] == "tolerance_period"
        assert details["interval_validation"] == "tolerance_period"
        assert details["normal_limit"] == {"months": 6, "kilometers": 10_000}
        assert details["tolerance"] == {"months": 1, "kilometers": 1_000}
        assert details["maximum_limit"] == {"months": 7, "kilometers": 11_000}
        assert details["calculated_limits"] == {
            "normal": {"date": "2026-07-01", "odometer_km": 50_000},
            "maximum": {"date": "2026-08-01", "odometer_km": 51_000},
        }
        assert history["summary"] == {
            "services_analyzed": 1,
            "compliant": 0,
            "tolerance_period": 1,
            "non_compliant": 0,
            "requires_review": 0,
            "result": "TOLERANCE_PERIOD",
            "message": "1 servicio utilizó el periodo de tolerancia.",
        }


def test_document_detail_and_persisted_validation_are_returned(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        event = create_event(
            db, vehicle, when=date(2026, 4, 1), mileage=47_000, filename="traceable.pdf"
        )
        history = validate_history(client, vehicle, [event])
        row = history["rows"][0]

        assert row["document"]["original_filename"] == "traceable.pdf"
        assert row["document"]["extracted_fields"]["vin"]["normalized_value"] == "VIN-HISTORY-001"
        assert row["document"]["extracted_text"].startswith("Servicio 2026-04-01")
        validation_id = row["validation"]["id"]
        persisted = client.get(f"/api/validations/{validation_id}")
        assert persisted.status_code == 200
        assert persisted.json()["analysis_details"] == row["validation"]["analysis_details"]


def test_history_rejects_event_from_another_vehicle(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        selected = create_vehicle(db)
        other = create_vehicle(db)
        event = create_event(
            db, other, when=date(2026, 4, 1), mileage=47_000, filename="other.pdf"
        )
        response = client.post(
            "/api/history-validations",
            json={"vehicle_id": selected.id, "event_ids": [event.id]},
        )
        assert response.status_code == 422
