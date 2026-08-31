from datetime import date

from app.services.maintenance_rules import add_calendar_months, evaluate_maintenance_window


def test_approves_when_kilometer_limit_is_reached():
    window = evaluate_maintenance_window(
        last_maintenance_date=date(2026, 2, 15),
        last_maintenance_odometer=40_000,
        document_date=date(2026, 5, 1),
        document_odometer=50_000,
    )
    assert window.kilometer_limit == 50_000
    assert window.meets_kilometer_condition is True
    assert window.meets_time_condition is False
    assert window.is_due is True


def test_approves_when_six_calendar_months_are_reached():
    window = evaluate_maintenance_window(
        last_maintenance_date=date(2026, 2, 15),
        last_maintenance_odometer=40_000,
        document_date=date(2026, 8, 15),
        document_odometer=45_000,
    )
    assert window.date_limit == date(2026, 8, 15)
    assert window.meets_kilometer_condition is False
    assert window.meets_time_condition is True
    assert window.is_due is True


def test_is_outside_window_when_neither_rule_is_reached():
    window = evaluate_maintenance_window(
        last_maintenance_date=date(2026, 2, 15),
        last_maintenance_odometer=40_000,
        document_date=date(2026, 5, 1),
        document_odometer=45_000,
    )
    assert window.is_due is False


def test_calendar_months_do_not_use_180_day_approximation():
    assert add_calendar_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert add_calendar_months(date(2024, 8, 31), 6) == date(2025, 2, 28)

