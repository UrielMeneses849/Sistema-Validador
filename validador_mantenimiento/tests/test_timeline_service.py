from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.service_event import ServiceEvent
from app.models.vehicle import Vehicle
from app.services.timeline_service import assess_service_event


def create_event(db: Session, vehicle: Vehicle, when: date, mileage: int) -> ServiceEvent:
    document = Document(
        vehicle_id=vehicle.id,
        original_filename=f"{when}.pdf",
        stored_filename=f"{when}-{mileage}.pdf",
        file_path=f"/private/tmp/{when}-{mileage}.pdf",
        mime_type="application/pdf",
        file_size=1,
    )
    db.add(document)
    db.flush()
    event = ServiceEvent(
        document_id=document.id,
        vehicle_id=vehicle.id,
        extraction_method="pdf_text",
        service_date=when,
        service_date_raw=when.isoformat(),
        mileage_km=mileage,
        mileage_raw=str(mileage),
        service_category="preventive_maintenance",
        service_type="Mantenimiento preventivo",
        resets_maintenance_interval=True,
        confidence="high",
        field_evidence={},
        warnings=[],
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def create_vehicle(db: Session) -> Vehicle:
    vehicle = Vehicle(
        internal_number="VEH-TIMELINE",
        plate="TML001",
        brand="Chevrolet",
        model="Captiva",
        year=2024,
        current_odometer=0,
        vehicle_condition="used",
        initial_odometer=0,
        fecha_inicio_contrato=date(2024, 1, 1),
    )
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


def test_required_timeline_cases(client, monkeypatch):
    # El fixture `client` deja configurado el engine de pruebas antes de estos imports.
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        first = create_event(db, vehicle, date(2024, 3, 23), 6399)
        first_assessment = assess_service_event(db, first)
        assert first_assessment.status == "COMPLIANT"
        assert first_assessment.service_sequence == "first"

        compliant = create_event(db, vehicle, date(2024, 8, 20), 15500)
        assessment = assess_service_event(db, compliant)
        assert assessment.status == "COMPLIANT"
        assert assessment.delta_km == 9101


def test_exceeded_mileage_time_and_regressive_cases(client):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        create_event(db, vehicle, date(2024, 1, 10), 6399)
        mileage = create_event(db, vehicle, date(2024, 5, 20), 17400)
        assert assess_service_event(db, mileage).status == "EXCEEDED_MILEAGE"

        vehicle_time = Vehicle(
            internal_number="VEH-TIME",
            plate="TME002",
            brand="Ford",
            model="Transit",
            year=2024,
            current_odometer=0,
            vehicle_condition="used",
            initial_odometer=0,
            fecha_inicio_contrato=date(2024, 1, 1),
        )
        db.add(vehicle_time)
        db.commit()
        db.refresh(vehicle_time)
        create_event(db, vehicle_time, date(2024, 1, 10), 10000)
        late = create_event(db, vehicle_time, date(2024, 8, 11), 15000)
        assert assess_service_event(db, late).status == "EXCEEDED_TIME"

        vehicle_reverse = Vehicle(
            internal_number="VEH-REVERSE",
            plate="REV003",
            brand="Kia",
            model="Rio",
            year=2024,
            current_odometer=0,
            vehicle_condition="used",
            initial_odometer=0,
            fecha_inicio_contrato=date(2024, 1, 1),
        )
        db.add(vehicle_reverse)
        db.commit()
        db.refresh(vehicle_reverse)
        create_event(db, vehicle_reverse, date(2024, 1, 10), 20000)
        reverse = create_event(db, vehicle_reverse, date(2024, 5, 10), 15000)
        assessment = assess_service_event(db, reverse)
        assert assessment.status == "REQUIRES_REVIEW"
        assert "possible_mileage_inconsistency" in assessment.reasons
