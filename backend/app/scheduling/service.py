"""Búsquedas programadas: reglas de cortesía (jitter, backoff por fallos, pausa automática, ventana horaria) y lanzamiento de cada ejecución.

Cada ejecución es un `SearchRun` normal. Quien programa es el trabajador (`enqueue_due`); varios trabajadores a la vez no duplican porque cada
disparo avanza `next_run_at` con un UPDATE condicionado al valor anterior (compare-and-set)."""
from __future__ import annotations

import logging
import random
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.config import settings
from app.db import ProfileRow, SavedSearch, SearchRun, engine, utcnow
from app.observability import metrics
from app.scheduling import windows

log = logging.getLogger(__name__)
STALE_RUN = timedelta(hours=2)          # una búsqueda 'running' más vieja que esto se considera abandonada
BUSY_RETRY_S = 300
PARAM_KEYS = ("keywords", "limit_per_source", "countries", "regions", "languages", "remote", "hours_old", "employment_types", "work_modes")


class ScheduleError(ValueError):
    pass


def clean_params(params: dict | None) -> dict:
    """Solo los parámetros de búsqueda conocidos, con tipos sanos; lo demás se ignora."""
    p = params or {}
    out: dict = {}
    for k in ("keywords", "countries", "regions", "languages", "employment_types", "work_modes"):
        if p.get(k) is not None:
            if not isinstance(p[k], list) or not all(isinstance(x, str) for x in p[k]):
                raise ScheduleError(f"{k} debe ser una lista de textos")
            vals = [x.strip() for x in p[k] if x.strip()][:20]
            out[k] = [v.upper() for v in vals] if k == "countries" else vals
    if p.get("limit_per_source") is not None:
        out["limit_per_source"] = max(1, min(100, int(p["limit_per_source"])))
    if p.get("remote") is not None:
        out["remote"] = bool(p["remote"])
    if p.get("hours_old") is not None:
        out["hours_old"] = max(1, min(24 * 90, int(p["hours_old"])))
    return out


def check_interval(minutes: int) -> int:
    if minutes < settings.schedule_min_interval_min:
        raise ScheduleError(f"el intervalo mínimo es {settings.schedule_min_interval_min} minutos (cortesía con los portales)")
    return minutes


def check_window(tz: str, start: str | None, end: str | None, days: list[int] | None) -> None:
    if not windows.valid_tz(tz):
        raise ScheduleError(f"zona horaria desconocida: {tz}")
    if (start is None) != (end is None):
        raise ScheduleError("la ventana necesita inicio y fin (HH:MM)")
    windows.parse_hhmm(start), windows.parse_hhmm(end)
    if days and any((not isinstance(d, int)) or d < 0 or d > 6 for d in days):
        raise ScheduleError("window_days: números 0 (lunes) a 6 (domingo)")


def _jittered(seconds: float, rng: Callable[[], float]) -> float:
    j = settings.schedule_jitter
    return seconds * (1 - j + 2 * j * rng())


def next_run(ss: SavedSearch, base: datetime, *, failures: int = 0, rng: Callable[[], float] = random.random) -> datetime:
    """Próximo disparo: base + intervalo con jitter (y backoff exponencial 2^fallos con tope), llevado al siguiente hueco de la ventana."""
    secs = ss.interval_minutes * 60 * (2 ** min(failures, 10) if failures else 1)
    secs = min(secs, settings.schedule_backoff_max_min * 60) if failures else secs
    return windows.next_window_open(base + timedelta(seconds=_jittered(secs, rng)), ss.timezone, ss.window_start, ss.window_end, ss.window_days or None)


def first_run(ss: SavedSearch, now: datetime, rng: Callable[[], float] = random.random) -> datetime:
    """La primera ejecución es pronto (hasta ~1 min de jitter) o, si está fuera de ventana, al abrirse esta."""
    return windows.next_window_open(now + timedelta(seconds=rng() * 60), ss.timezone, ss.window_start, ss.window_end, ss.window_days or None)


def run_failed(run: SearchRun) -> bool:
    """Una ejecución falló si lo hizo entera o si NINGUNA fuente respondió (todas error/bloqueo): el usuario no recibió nada fiable."""
    if run.status == "failed":
        return True
    srcs = list((run.sources or {}).values())
    return bool(srcs) and all(v.get("status") == "error" or v.get("outcome") in ("error", "blocked") for v in srcs)


def _active_run(s: Session, user_id: int, now: datetime) -> bool:
    runs = s.exec(select(SearchRun).where(SearchRun.user_id == user_id, col(SearchRun.status).in_(["queued", "running"]))).all()
    return any(windows.aware(r.created_at) > now - STALE_RUN for r in runs)


def launch(s: Session, ss: SavedSearch, now: datetime) -> SearchRun:
    """Crea el SearchRun y lo encola (el trabajador lo ejecutará)."""
    from app.worker import queue
    run = SearchRun(user_id=ss.user_id, params={**(ss.params or {}), "saved_search_id": ss.id, "scheduled": True}, created_at=now)
    s.add(run)
    s.flush()
    queue.enqueue(s, "search", ref_id=run.id, user_id=ss.user_id, payload={"run_id": run.id}, dedup_key=f"search:{run.id}", now=now, commit=False)
    return run


def enqueue_due(now: datetime | None = None, *, rng: Callable[[], float] = random.random, limit: int = 100) -> int:
    """Lanza las búsquedas guardadas vencidas. Devuelve cuántas ejecuciones creó."""
    now = now or utcnow()
    launched = 0
    with Session(engine) as s:
        due = s.exec(select(SavedSearch).where(SavedSearch.active == True, col(SavedSearch.next_run_at) <= now)   # noqa: E712
                     .order_by(col(SavedSearch.next_run_at)).limit(limit)).all()
        ids = [(d.id, d.next_run_at) for d in due]
    for sid, seen_next in ids:
        with Session(engine) as s:
            ss = s.get(SavedSearch, sid)
            if ss is None or not ss.active or ss.next_run_at != seen_next:
                continue
            reason: str | None = None
            if not windows.in_window(now, ss.timezone, ss.window_start, ss.window_end, ss.window_days or None):
                new_next = windows.next_window_open(now, ss.timezone, ss.window_start, ss.window_end, ss.window_days or None) + timedelta(seconds=rng() * 120)
                reason = "fuera de ventana"
            elif s.get(ProfileRow, ss.user_id) is None or not s.get(ProfileRow, ss.user_id).data:
                new_next, reason = now + timedelta(hours=1), "sin CV/perfil"
                ss.last_error = "Sube un CV para que la búsqueda programada pueda ejecutarse"
            elif _active_run(s, ss.user_id, now):
                new_next, reason = now + timedelta(seconds=BUSY_RETRY_S * (0.8 + 0.4 * rng())), "búsqueda en curso"
            else:
                new_next = next_run(ss, now, rng=rng)            # provisional: al terminar se recalcula desde el final real
            # compare-and-set: si otro trabajador ya la disparó, next_run_at cambió y no hacemos nada
            won = s.execute(update(SavedSearch).where(SavedSearch.id == sid, SavedSearch.next_run_at == seen_next, SavedSearch.active == True)   # noqa: E712
                            .values(next_run_at=new_next, updated_at=now, last_error=ss.last_error)).rowcount
            if won != 1:
                s.rollback()
                continue
            if reason is None:
                s.refresh(ss)
                launch(s, ss, now)
                launched += 1
                metrics.inc("jh_scheduled_runs_total")
            else:
                metrics.inc("jh_scheduled_skips_total", reason=reason)
            s.commit()
    return launched


def record_result(s: Session, saved_search_id: int, run: SearchRun, now: datetime | None = None, rng: Callable[[], float] = random.random) -> SavedSearch | None:
    """Anota cómo terminó una ejecución: racha de fallos, backoff, pausa automática (con aviso) y próximo disparo."""
    now = now or utcnow()
    ss = s.get(SavedSearch, saved_search_id)
    if ss is None or ss.user_id != run.user_id:
        return None
    if ss.last_run_id == run.id:                  # ya contabilizada (reintento tras una caída): idempotente
        return ss
    ss.last_run_at, ss.last_run_id, ss.total_runs, ss.updated_at = now, run.id, ss.total_runs + 1, now
    if run_failed(run):
        ss.failure_streak += 1
        ss.last_error = (run.error or "ninguna fuente respondió")[:300]
        if ss.failure_streak >= settings.schedule_max_failures and ss.active:
            ss.active = False
            ss.paused_reason = f"pausada automáticamente tras {ss.failure_streak} fallos seguidos: {ss.last_error}"[:300]
            from app.notifications import service as notif
            notif.notify_system(s, ss.user_id, "search_paused", f"Búsqueda programada pausada: {ss.name}",
                                f"{ss.paused_reason}. Revísala y reactívala cuando quieras.", data={"saved_search_id": ss.id},
                                dedup_key=f"paused:{ss.id}:{ss.total_runs}", now=now)
            metrics.inc("jh_saved_search_paused_total")
        else:
            ss.next_run_at = next_run(ss, now, failures=ss.failure_streak, rng=rng)
    else:
        ss.failure_streak, ss.last_error = 0, None
        ss.next_run_at = next_run(ss, now, rng=rng)
    s.add(ss)
    s.commit()
    return ss


def manual_runs_since(s: Session, user_id: int, since: datetime) -> int:
    """Búsquedas lanzadas a mano (las programadas no gastan la cuota diaria del usuario)."""
    rows = s.exec(select(SearchRun.params).where(SearchRun.user_id == user_id, col(SearchRun.created_at) >= since)).all()
    return sum(1 for p in rows if not (p or {}).get("scheduled"))
