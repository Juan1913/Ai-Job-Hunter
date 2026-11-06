"""Zonas horarias, ventanas horarias permitidas y silencio nocturno. Todo recibe el instante como argumento (reloj inyectable)."""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def tz_of(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo("UTC")


def valid_tz(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def parse_hhmm(v: str | None) -> time | None:
    if not v:
        return None
    m = _HHMM.match(v)
    if not m:
        raise ValueError(f"hora inválida '{v}': usa HH:MM")
    return time(int(m.group(1)), int(m.group(2)))


def aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def in_window(now: datetime, tz: str, start: str | None, end: str | None, days: list[int] | None = None) -> bool:
    """¿`now` cae dentro de la ventana [start, end) en la zona `tz` (y en un día permitido)? Admite ventanas que cruzan la medianoche
    (22:00-06:00): su tramo de madrugada cuenta como parte del día en que empezó la ventana."""
    local = aware(now).astimezone(tz_of(tz))
    s, e = parse_hhmm(start), parse_hhmm(end)
    day = local.weekday()
    if s is None or e is None or s == e:
        return not days or day in days
    t = local.time().replace(second=0, microsecond=0)
    if s < e:
        return (s <= t < e) and (not days or day in days)
    if t >= s:
        return not days or day in days
    if t < e:
        return not days or ((day - 1) % 7) in days
    return False


def next_window_open(now: datetime, tz: str, start: str | None, end: str | None, days: list[int] | None = None) -> datetime:
    """Primer instante >= now dentro de la ventana (UTC). Si ya está dentro, devuelve now."""
    now = aware(now)
    if in_window(now, tz, start, end, days):
        return now
    z = tz_of(tz)
    s = parse_hhmm(start) or time(0, 0)
    local = now.astimezone(z)
    for d in range(0, 9):
        cand = datetime.combine((local + timedelta(days=d)).date(), s, tzinfo=z)
        if cand > local and in_window(cand, tz, start, end, days):
            return cand.astimezone(timezone.utc)
    return now + timedelta(days=1)          # configuración imposible (p. ej. días vacíos con ventana vacía): reintenta mañana


def in_quiet(now: datetime, tz: str, start: str | None, end: str | None) -> bool:
    """Silencio nocturno: [start, end) en la zona del usuario, p. ej. 22:00-07:00. Sin configurar = nunca silencio."""
    if not start or not end or start == end:
        return False
    return in_window(now, tz, start, end, None)


def quiet_ends_at(now: datetime, tz: str, start: str | None, end: str | None) -> datetime | None:
    """Fin del silencio vigente (UTC), o None si ahora no hay silencio."""
    if not in_quiet(now, tz, start, end):
        return None
    z = tz_of(tz)
    e = parse_hhmm(end)
    local = aware(now).astimezone(z)
    cand = datetime.combine(local.date(), e, tzinfo=z)
    if cand <= local:
        cand = datetime.combine(local.date() + timedelta(days=1), e, tzinfo=z)
    return cand.astimezone(timezone.utc)
