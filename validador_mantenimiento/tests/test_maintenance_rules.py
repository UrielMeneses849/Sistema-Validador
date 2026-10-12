from datetime import date

from app.services.maintenance_rules import add_calendar_months, evaluate_maintenance_window


def test_exact_kilometer_limit_is_compliant_before_date_limit():
    window = evaluate_maintenance_window(
        last_maintenance_date=date(2026, 2, 15),
        last_maintenance_odometer=40_000,
        document_date=date(2026, 5, 1),
        document_odometer=50_000,
    )
    assert window.normal_kilometer_limit == 50_000
    assert window.kilometer_limit == 51_000
    assert window.meets_kilometer_condition is True
    assert window.meets_time_condition is True
    assert window.is_compliant is True


def test_exact_six_calendar_month_limit_is_compliant_below_kilometer_limit():
    window = evaluate_maintenance_window(
        last_maintenance_date=date(2026, 2, 15),
        last_maintenance_odometer=40_000,
        document_date=date(2026, 8, 15),
        document_odometer=45_000,
    )
    assert window.normal_date_limit == date(2026, 8, 15)
    assert window.date_limit == date(2026, 9, 15)
    assert window.meets_kilometer_condition is True
    assert window.meets_time_condition is True
    assert window.is_compliant is True


def test_early_service_is_compliant_when_neither_maximum_is_reached():
    window = evaluate_maintenance_window(
        last_maintenance_date=date(2026, 2, 15),
        last_maintenance_odometer=40_000,
        document_date=date(2026, 5, 1),
        document_odometer=45_000,
    )
    assert window.meets_kilometer_condition is True
    assert window.meets_time_condition is True
    assert window.is_compliant is True


def test_service_after_normal_limit_and_at_maximum_is_in_tolerance():
    window = evaluate_maintenance_window(
        last_maintenance_date=date(2026, 1, 1),
        last_maintenance_odometer=40_000,
        document_date=date(2026, 8, 1),
        document_odometer=51_000,
    )
    assert window.is_compliant is False
    assert window.is_within_tolerance is True
    assert window.meets_normal_kilometer_condition is False
    assert window.meets_normal_time_condition is False
    assert window.meets_kilometer_condition is True
    assert window.meets_time_condition is True


def test_calendar_months_do_not_use_180_day_approximation():
    assert add_calendar_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert add_calendar_months(date(2024, 8, 31), 6) == date(2025, 2, 28)
