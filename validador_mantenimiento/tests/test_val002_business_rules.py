from __future__ import annotations

from datetime import date
from itertools import count

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.manufacturer_maintenance_rule import ManufacturerMaintenanceRule
from app.models.service_event import ServiceEvent
from app.models.vehicle import Vehicle
from app.services.timeline_service import assess_service_event


_sequence = count(1)


def create_vehicle(
    db: Session,
    *,
    condition: str = "used",
    brand: str = "Ford",
    start: date | None = date(2026, 1, 1),
    initial_odometer: int | None = 40_000,
) -> Vehicle:
    suffix = next(_sequence)
    vehicle = Vehicle(
        internal_number=f"VAL002-{suffix}",
        plate=f"V{suffix:06d}",
        brand=brand,
        model="Unidad de prueba",
        year=2026,
        current_odometer=initial_odometer or 0,
        vehicle_condition=condition,
        initial_odometer=initial_odometer,
        fecha_inicio_contrato=start,
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
    method: str = "pdf_text",
    category: str = "preventive_maintenance",
    resets: bool = True,
    requires_review: bool = False,
) -> ServiceEvent:
    suffix = next(_sequence)
    document = Document(
        vehicle_id=vehicle.id,
        original_filename=f"service-{suffix}.pdf",
        stored_filename=f"service-{suffix}.pdf",
        file_path=f"/private/tmp/val002-service-{suffix}.pdf",
        mime_type="application/pdf",
        file_size=1,
    )
    db.add(document)
    db.flush()
    event = ServiceEvent(
        document_id=document.id,
        vehicle_id=vehicle.id,
        extraction_method=method,
        service_date=when,
        service_date_raw=when.isoformat(),
        mileage_km=mileage,
        mileage_raw=str(mileage),
        service_category=category,
        service_type="Mantenimiento preventivo" if category == "preventive_maintenance" else "Reparación",
        resets_maintenance_interval=resets,
        confidence="high",
        requires_human_review=requires_review,
        field_evidence={},
        warnings=[],
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def create_policy(
    db: Session,
    *,
    brand: str = "FORD",
    months: int = 12,
    kilometers: int = 12_000,
    active: bool = True,
) -> ManufacturerMaintenanceRule:
    rule = ManufacturerMaintenanceRule(
        brand=brand,
        interval_months=months,
        interval_km=kilometers,
        active=active,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def test_used_first_service_inside_six_months_and_ten_thousand_km(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 4, 1), mileage=49_000)
        )

        assert assessment.status == "COMPLIANT"
        assert assessment.service_sequence == "first"
        assert assessment.previous.source == "contract_start"
        assert assessment.policy.source == "used_vehicle_default"


def test_used_first_service_exceeds_only_mileage(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 4, 1), mileage=51_001)
        )

        assert assessment.status == "EXCEEDED_MILEAGE"
        assert assessment.meets_kilometer_condition is False
        assert assessment.meets_time_condition is True


def test_used_first_service_exceeds_only_time(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 8, 2), mileage=45_000)
        )

        assert assessment.status == "EXCEEDED_TIME"
        assert assessment.meets_kilometer_condition is True
        assert assessment.meets_time_condition is False


def test_used_first_service_exceeds_both_limits(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 8, 2), mileage=51_001)
        )

        assert assessment.status == "EXCEEDED_BOTH"
        assert assessment.meets_kilometer_condition is False
        assert assessment.meets_time_condition is False


def test_used_exact_six_month_limit_is_valid(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 7, 1), mileage=44_000)
        )

        assert assessment.status == "COMPLIANT"
        assert assessment.normal_date_limit == date(2026, 7, 1)
        assert assessment.date_limit == date(2026, 8, 1)


def test_used_exact_ten_thousand_km_limit_is_valid(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 4, 1), mileage=50_000)
        )

        assert assessment.status == "COMPLIANT"
        assert assessment.normal_kilometer_limit == 50_000
        assert assessment.kilometer_limit == 51_000


def test_used_second_service_uses_previous_maintenance_instead_of_contract(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        previous = create_event(db, vehicle, when=date(2026, 5, 15), mileage=48_000)
        current = create_event(db, vehicle, when=date(2026, 11, 15), mileage=58_000)
        assessment = assess_service_event(db, current)

        assert assessment.status == "COMPLIANT"
        assert assessment.service_sequence == "subsequent"
        assert assessment.previous.source_id == previous.id
        assert assessment.normal_date_limit == date(2026, 11, 15)
        assert assessment.date_limit == date(2026, 12, 15)
        assert assessment.normal_kilometer_limit == 58_000
        assert assessment.kilometer_limit == 59_000


def test_new_vehicle_resolves_existing_policy_case_insensitively(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        rule = create_policy(db, brand="FORD", months=12, kilometers=12_000)
        vehicle = create_vehicle(db, condition="new", brand="  Ford ")
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 6, 1), mileage=45_000)
        )

        assert assessment.status == "COMPLIANT"
        assert assessment.policy.source == "manufacturer"
        assert assessment.policy.rule_id == rule.id
        assert assessment.policy.brand == "FORD"


def test_new_first_service_inside_manufacturer_policy_is_compliant(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        create_policy(db, months=15, kilometers=12_000)
        vehicle = create_vehicle(db, condition="new")
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2027, 3, 31), mileage=52_000)
        )

        assert assessment.status == "COMPLIANT"
        assert assessment.service_sequence == "first"
        assert assessment.normal_date_limit == date(2027, 4, 1)
        assert assessment.date_limit == date(2027, 5, 1)
        assert assessment.normal_kilometer_limit == 52_000
        assert assessment.kilometer_limit == 53_000


def test_new_service_exceeds_manufacturer_kilometer_limit(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        create_policy(db, months=12, kilometers=12_000)
        vehicle = create_vehicle(db, condition="new")
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 6, 1), mileage=53_001)
        )

        assert assessment.status == "EXCEEDED_MILEAGE"


def test_new_service_exceeds_manufacturer_time_limit(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        create_policy(db, months=12, kilometers=12_000)
        vehicle = create_vehicle(db, condition="new")
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2027, 2, 2), mileage=50_000)
        )

        assert assessment.status == "EXCEEDED_TIME"


def test_new_second_service_uses_previous_maintenance(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        create_policy(db, months=12, kilometers=12_000)
        vehicle = create_vehicle(db, condition="new")
        previous = create_event(db, vehicle, when=date(2026, 8, 1), mileage=48_000)
        current = create_event(db, vehicle, when=date(2027, 7, 31), mileage=59_000)
        assessment = assess_service_event(db, current)

        assert assessment.status == "COMPLIANT"
        assert assessment.service_sequence == "subsequent"
        assert assessment.previous.source_id == previous.id
        assert assessment.normal_date_limit == date(2027, 8, 1)
        assert assessment.date_limit == date(2027, 9, 1)


def test_new_vehicle_without_active_brand_policy_requires_review(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        create_policy(db, active=False)
        vehicle = create_vehicle(db, condition="new")
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 6, 1), mileage=45_000)
        )

        assert assessment.status == "REQUIRES_REVIEW"
        assert assessment.reasons == [
            "No existe una política de mantenimiento configurada para la marca seleccionada."
        ]


def test_legacy_unknown_condition_requires_review(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(
            db, condition="unknown", start=None, initial_odometer=None
        )
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 6, 1), mileage=45_000)
        )

        assert assessment.status == "REQUIRES_REVIEW"
        assert assessment.service_sequence == "first"


def test_initial_odometer_is_immutable_and_maintenance_only_updates_current_value(client: TestClient):
    vehicle_response = client.post(
        "/api/vehicles",
        json={
            "brand": "Nissan",
            "model": "NP300",
            "kilometraje": 40_000,
            "fecha_factura_origen": "2025-12-01",
            "fecha_inicio_contrato": "2026-01-01",
            "fecha_fin_contrato": "2027-01-01",
            "vehicle_condition": "used",
            "initial_odometer": 40_000,
        },
    )
    assert vehicle_response.status_code == 201, vehicle_response.text
    vehicle = vehicle_response.json()

    maintenance = client.post(
        f"/api/vehicles/{vehicle['id']}/maintenances",
        json={
            "maintenance_date": "2026-05-01",
            "odometer": 48_000,
            "maintenance_type": "Preventivo",
        },
    )
    assert maintenance.status_code == 201, maintenance.text
    saved = client.get(f"/api/vehicles/{vehicle['id']}").json()
    assert saved["initial_odometer"] == 40_000
    assert saved["current_odometer"] == saved["kilometraje"] == 48_000

    changed = client.put(
        f"/api/vehicles/{vehicle['id']}", json={"initial_odometer": 40_001}
    )
    assert changed.status_code == 422
    assert "no se puede modificar" in changed.json()["detail"]


def test_repair_or_campaign_does_not_restart_interval(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        repair = create_event(
            db,
            vehicle,
            when=date(2026, 3, 1),
            mileage=45_000,
            category="repair",
            resets=False,
        )
        assert assess_service_event(db, repair).status == "NOT_MAINTENANCE_EVENT"

        current = create_event(db, vehicle, when=date(2026, 6, 1), mileage=49_000)
        assessment = assess_service_event(db, current)
        assert assessment.service_sequence == "first"
        assert assessment.previous.source == "contract_start"
        assert assessment.kilometer_limit == 51_000


def test_event_marked_reset_but_not_preventive_does_not_restart_interval(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        campaign = create_event(
            db,
            vehicle,
            when=date(2026, 3, 1),
            mileage=45_000,
            category="campaign",
            resets=True,
        )
        assert assess_service_event(db, campaign).status == "NOT_MAINTENANCE_EVENT"

        current = create_event(db, vehicle, when=date(2026, 6, 1), mileage=49_000)
        assert assess_service_event(db, current).previous.source == "contract_start"


def test_event_that_requires_review_remains_an_eligible_baseline_with_valid_fields(
    client: TestClient,
):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        previous = create_event(
            db,
            vehicle,
            when=date(2026, 3, 1),
            mileage=45_000,
            requires_review=True,
        )
        current = create_event(db, vehicle, when=date(2026, 6, 1), mileage=49_000)

        assessment = assess_service_event(db, current)
        assert assessment.service_sequence == "subsequent"
        assert assessment.previous.source == "service_event"
        assert assessment.previous.source_id == previous.id
        assert assessment.delta_km == 4_000


def test_early_service_is_valid(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 2, 1), mileage=41_000)
        )

        assert assessment.status == "COMPLIANT"


def test_regressive_mileage_requires_review(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 2, 1), mileage=39_999)
        )

        assert assessment.status == "REQUIRES_REVIEW"
        assert "possible_mileage_inconsistency" in assessment.reasons


def test_regressive_date_requires_review(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db)
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2025, 12, 31), mileage=40_100)
        )

        assert assessment.status == "REQUIRES_REVIEW"
        assert "anterior a la fecha de referencia" in assessment.reasons[0]


def test_manufacturer_policy_crud_normalizes_brand_and_soft_deletes(client: TestClient):
    created = client.post(
        "/api/manufacturer-rules",
        json={"brand": "  Ford   Motor  ", "interval_months": 12, "interval_km": 10_000},
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["brand"] == "FORD MOTOR"

    duplicate = client.post(
        "/api/manufacturer-rules",
        json={"brand": "ford motor", "interval_months": 6, "interval_km": 5_000},
    )
    assert duplicate.status_code == 409
    assert len(client.get("/api/manufacturer-rules?active=true").json()) == 1

    updated = client.put(
        f"/api/manufacturer-rules/{rule['id']}",
        json={"interval_months": 15, "interval_km": 12_000},
    )
    assert updated.status_code == 200
    assert updated.json()["interval_months"] == 15
    assert updated.json()["interval_km"] == 12_000

    deactivated = client.delete(f"/api/manufacturer-rules/{rule['id']}")
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False
    assert client.get("/api/manufacturer-rules?active=true").json() == []
    assert len(client.get("/api/manufacturer-rules?active=false").json()) == 1

    reactivated = client.put(
        f"/api/manufacturer-rules/{rule['id']}", json={"active": True}
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["active"] is True


def test_manufacturer_policy_rejects_non_positive_intervals(client: TestClient):
    response = client.post(
        "/api/manufacturer-rules",
        json={"brand": "Ford", "interval_months": 0, "interval_km": -1},
    )
    assert response.status_code == 422


def test_used_vehicle_ignores_registered_manufacturer_policy(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        create_policy(db, months=1, kilometers=100)
        vehicle = create_vehicle(db, condition="used")
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 2, 15), mileage=41_000)
        )

        assert assessment.status == "COMPLIANT"
        assert assessment.policy.source == "used_vehicle_default"
        assert assessment.policy.interval_months == 6
        assert assessment.policy.interval_km == 10_000


def test_business_result_is_independent_of_extraction_method(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        statuses = []
        traces = []
        for method in ("pdf_text", "tesseract", "manual"):
            vehicle = create_vehicle(db)
            assessment = assess_service_event(
                db,
                create_event(
                    db,
                    vehicle,
                    when=date(2026, 4, 1),
                    mileage=49_000,
                    method=method,
                ),
            )
            statuses.append(assessment.status)
            traces.append((assessment.service_sequence, assessment.policy.source))

        assert statuses == ["COMPLIANT", "COMPLIANT", "COMPLIANT"]
        assert traces == [
            ("first", "used_vehicle_default"),
            ("first", "used_vehicle_default"),
            ("first", "used_vehicle_default"),
        ]


@pytest.mark.parametrize(
    ("when", "mileage", "expected"),
    [
        pytest.param(date(2026, 7, 1), 45_000, "COMPLIANT", id="exact-six-months"),
        pytest.param(date(2026, 5, 1), 50_000, "COMPLIANT", id="exact-ten-thousand-km"),
        pytest.param(date(2026, 7, 15), 45_000, "TOLERANCE_PERIOD", id="days-after-six-months"),
        pytest.param(date(2026, 5, 1), 50_500, "TOLERANCE_PERIOD", id="ten-thousand-five-hundred-km"),
        pytest.param(date(2026, 8, 1), 45_000, "TOLERANCE_PERIOD", id="exact-seven-months"),
        pytest.param(date(2026, 5, 1), 51_000, "TOLERANCE_PERIOD", id="exact-eleven-thousand-km"),
        pytest.param(date(2026, 8, 2), 45_000, "EXCEEDED_TIME", id="over-seven-months"),
        pytest.param(date(2026, 5, 1), 51_001, "EXCEEDED_MILEAGE", id="over-eleven-thousand-km"),
        pytest.param(date(2026, 8, 2), 51_001, "EXCEEDED_BOTH", id="over-both-maximums"),
    ],
)
def test_m2_normal_tolerance_and_maximum_boundaries(
    client: TestClient, when: date, mileage: int, expected: str
):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db, condition="used")
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=when, mileage=mileage)
        )

        assert assessment.status == expected
        assert assessment.normal_date_limit == date(2026, 7, 1)
        assert assessment.date_limit == date(2026, 8, 1)
        assert assessment.normal_kilometer_limit == 50_000
        assert assessment.kilometer_limit == 51_000


@pytest.mark.parametrize(
    ("mileage", "expected"),
    [
        pytest.param(46_500, "TOLERANCE_PERIOD", id="six-thousand-five-hundred-km"),
        pytest.param(47_000, "TOLERANCE_PERIOD", id="exact-seven-thousand-km"),
        pytest.param(47_001, "EXCEEDED_MILEAGE", id="over-seven-thousand-km"),
    ],
)
def test_m1_adds_global_tolerance_to_six_month_six_thousand_policy(
    client: TestClient, mileage: int, expected: str
):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        policy = create_policy(db, months=6, kilometers=6_000)
        vehicle = create_vehicle(db, condition="new")
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2026, 5, 1), mileage=mileage)
        )

        assert assessment.status == expected
        assert assessment.normal_kilometer_limit == 46_000
        assert assessment.kilometer_limit == 47_000
        assert policy.interval_months == 6
        assert policy.interval_km == 6_000


def test_m1_twelve_month_ten_thousand_policy_has_thirteen_eleven_maximum(
    client: TestClient,
):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        policy = create_policy(db, months=12, kilometers=10_000)
        vehicle = create_vehicle(db, condition="new")
        assessment = assess_service_event(
            db, create_event(db, vehicle, when=date(2027, 2, 1), mileage=51_000)
        )

        assert assessment.status == "TOLERANCE_PERIOD"
        assert assessment.normal_date_limit == date(2027, 1, 1)
        assert assessment.date_limit == date(2027, 2, 1)
        assert assessment.normal_kilometer_limit == 50_000
        assert assessment.kilometer_limit == 51_000
        assert policy.interval_months == 12
        assert policy.interval_km == 10_000


def test_tolerance_maintenance_can_be_next_interval_reference(client: TestClient):
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        vehicle = create_vehicle(db, condition="used")
        tolerance_event = create_event(
            db, vehicle, when=date(2026, 5, 1), mileage=50_500
        )
        assert assess_service_event(db, tolerance_event).status == "TOLERANCE_PERIOD"

        next_event = create_event(
            db, vehicle, when=date(2026, 9, 1), mileage=59_000
        )
        assessment = assess_service_event(db, next_event)

        assert assessment.status == "COMPLIANT"
        assert assessment.service_sequence == "subsequent"
        assert assessment.previous.source_id == tolerance_event.id
        assert assessment.delta_km == 8_500


def test_val002_sqlite_columns_are_added_without_changing_legacy_data(tmp_path, monkeypatch):
    from app.database import init_db as init_db_module

    legacy_engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with legacy_engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE vehicles (id INTEGER PRIMARY KEY, brand VARCHAR(80) NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE maintenances (id INTEGER PRIMARY KEY, vehicle_id INTEGER NOT NULL)"
        )
        connection.exec_driver_sql("INSERT INTO vehicles (id, brand) VALUES (7, 'Legacy')")
        connection.exec_driver_sql("INSERT INTO maintenances (id, vehicle_id) VALUES (9, 7)")

    monkeypatch.setattr(init_db_module, "engine", legacy_engine)
    init_db_module._add_val002_columns()

    vehicle_columns = {column["name"] for column in inspect(legacy_engine).get_columns("vehicles")}
    maintenance_columns = {
        column["name"] for column in inspect(legacy_engine).get_columns("maintenances")
    }
    assert {"vehicle_condition", "initial_odometer"}.issubset(vehicle_columns)
    assert "resets_maintenance_interval" in maintenance_columns
    with legacy_engine.connect() as connection:
        vehicle_row = connection.exec_driver_sql(
            "SELECT id, brand, vehicle_condition, initial_odometer FROM vehicles"
        ).one()
        maintenance_row = connection.exec_driver_sql(
            "SELECT id, vehicle_id, resets_maintenance_interval FROM maintenances"
        ).one()
    assert vehicle_row == (7, "Legacy", "unknown", None)
    assert maintenance_row == (9, 7, 1)
