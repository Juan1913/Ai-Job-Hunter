from datetime import datetime, timezone

from app.scheduling import windows as w


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_simple_window_in_user_timezone():
    # 12:00 UTC = 07:00 en Bogotá (UTC-5)
    assert w.in_window(utc(2026, 11, 2, 12), "America/Bogota", "07:00", "20:00")
    assert not w.in_window(utc(2026, 11, 2, 11, 59), "America/Bogota", "07:00", "20:00")
    assert not w.in_window(utc(2026, 11, 3, 1, 0), "America/Bogota", "07:00", "20:00")      # 20:00 local: el fin es exclusivo


def test_overnight_window_and_weekdays():
    assert w.in_window(utc(2026, 11, 3, 3, 0), "UTC", "22:00", "06:00")
    assert not w.in_window(utc(2026, 11, 3, 12, 0), "UTC", "22:00", "06:00")
    # solo lunes (0): la madrugada del martes pertenece a la ventana que empezó el lunes
    assert w.in_window(utc(2026, 11, 3, 3, 0), "UTC", "22:00", "06:00", [0])
    assert not w.in_window(utc(2026, 11, 4, 3, 0), "UTC", "22:00", "06:00", [0])
    assert not w.in_window(utc(2026, 11, 3, 12, 0), "UTC", None, None, [0])               # solo días: martes fuera


def test_next_window_open_skips_to_the_next_allowed_day():
    # martes 12:00 UTC, ventana lunes-viernes 09:00-10:00 UTC solo miércoles
    nxt = w.next_window_open(utc(2026, 11, 3, 12), "UTC", "09:00", "10:00", [2])
    assert nxt == utc(2026, 11, 4, 9)
    assert w.next_window_open(utc(2026, 11, 4, 9, 30), "UTC", "09:00", "10:00", [2]) == utc(2026, 11, 4, 9, 30)


def test_quiet_hours_end_in_user_timezone():
    # silencio 22:00-07:00 en Bogotá; 03:00 UTC = 22:00 Bogotá (inicio del silencio)
    assert w.in_quiet(utc(2026, 11, 3, 3), "America/Bogota", "22:00", "07:00")
    assert w.quiet_ends_at(utc(2026, 11, 3, 3), "America/Bogota", "22:00", "07:00") == utc(2026, 11, 3, 12)
    assert not w.in_quiet(utc(2026, 11, 3, 13), "America/Bogota", "22:00", "07:00")
    assert w.quiet_ends_at(utc(2026, 11, 3, 13), "America/Bogota", "22:00", "07:00") is None
    assert not w.in_quiet(utc(2026, 11, 3, 3), "UTC", None, None)


def test_bad_values():
    assert not w.valid_tz("Mars/Olympus")
    import pytest
    with pytest.raises(ValueError):
        w.parse_hhmm("25:00")
