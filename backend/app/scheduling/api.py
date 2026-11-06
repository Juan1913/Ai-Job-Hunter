"""CRUD de búsquedas programadas: /api/v1/me/saved-searches. Las ejecuta el trabajador (`python -m app.worker`), no la API."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from app.api.deps import current_user
from app.config import settings
from app.db import SavedSearch, User, get_session, utcnow
from app.notifications import service as notif
from app.scheduling import service as sched
from app.scheduling import windows

router = APIRouter(prefix="/me/saved-searches", tags=["saved-searches"])


class SavedSearchIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    keywords: list[str] | None = None             # si no, se usan los roles objetivo de las preferencias
    limit_per_source: int = Field(25, ge=1, le=100)
    countries: list[str] | None = None            # si no, los de las preferencias
    regions: list[str] | None = None
    languages: list[str] | None = None
    remote: bool | None = None
    hours_old: int | None = Field(None, ge=1, le=24 * 90)
    employment_types: list[str] | None = None
    work_modes: list[str] | None = None
    interval_minutes: int = Field(360, ge=1, le=60 * 24 * 30)
    timezone: str | None = None                    # por defecto la zona de tus preferencias de avisos
    window_start: str | None = None                # "HH:MM": solo buscar dentro de esta ventana
    window_end: str | None = None
    window_days: list[int] = Field(default_factory=list)   # 0=lunes..6=domingo; vacío = todos
    active: bool = True


class SavedSearchPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    keywords: list[str] | None = None
    limit_per_source: int | None = Field(None, ge=1, le=100)
    countries: list[str] | None = None
    regions: list[str] | None = None
    languages: list[str] | None = None
    remote: bool | None = None
    hours_old: int | None = Field(None, ge=1, le=24 * 90)
    employment_types: list[str] | None = None
    work_modes: list[str] | None = None
    interval_minutes: int | None = Field(None, ge=1, le=60 * 24 * 30)
    timezone: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    window_days: list[int] | None = None
    active: bool | None = None


def _iso(dt):
    return windows.aware(dt).isoformat() if dt else None


def _out(ss: SavedSearch) -> dict:
    return {"id": ss.id, "name": ss.name, "params": ss.params, "interval_minutes": ss.interval_minutes, "timezone": ss.timezone,
            "window_start": ss.window_start, "window_end": ss.window_end, "window_days": ss.window_days or [], "active": ss.active,
            "paused_reason": ss.paused_reason, "next_run_at": _iso(ss.next_run_at), "last_run_at": _iso(ss.last_run_at), "last_run_id": ss.last_run_id,
            "failure_streak": ss.failure_streak, "total_runs": ss.total_runs, "last_error": ss.last_error, "created_at": _iso(ss.created_at)}


def _own(s: Session, user: User, sid: int) -> SavedSearch:
    ss = s.get(SavedSearch, sid)
    if not ss or ss.user_id != user.id:
        raise HTTPException(404, "Búsqueda programada no encontrada")
    return ss


def _bad(e: Exception):
    return HTTPException(422, str(e))


@router.get("")
def list_saved(user: User = Depends(current_user), s: Session = Depends(get_session)):
    return [_out(x) for x in s.exec(select(SavedSearch).where(SavedSearch.user_id == user.id).order_by(col(SavedSearch.id))).all()]


@router.get("/limits")
def limits():
    return {"min_interval_minutes": settings.schedule_min_interval_min, "max_per_user": settings.schedule_max_per_user,
            "pause_after_failures": settings.schedule_max_failures}


@router.get("/worker")
def worker_status(user: User = Depends(current_user)):
    """¿Hay un trabajador vivo? Sin él las búsquedas programadas NO se ejecutan (la API no las lanza)."""
    from app.worker import health
    alive = health.alive_workers()
    return {"alive": bool(alive), "count": len(alive)}


@router.post("", status_code=201)
def create(body: SavedSearchIn, user: User = Depends(current_user), s: Session = Depends(get_session)):
    n = s.exec(select(func.count()).select_from(SavedSearch).where(SavedSearch.user_id == user.id)).one()
    if n >= settings.schedule_max_per_user:
        raise HTTPException(409, f"Máximo {settings.schedule_max_per_user} búsquedas programadas")
    raw = body.model_dump(exclude_none=True)
    tz = body.timezone or notif.get_prefs(s, user.id).timezone
    try:
        params = sched.clean_params({k: raw.get(k) for k in sched.PARAM_KEYS})
        sched.check_interval(body.interval_minutes)
        sched.check_window(tz, body.window_start, body.window_end, body.window_days)
    except ValueError as e:
        raise _bad(e)
    now = utcnow()
    ss = SavedSearch(user_id=user.id, name=body.name.strip(), params=params, interval_minutes=body.interval_minutes, timezone=tz,
                     window_start=body.window_start, window_end=body.window_end, window_days=sorted(set(body.window_days)), active=body.active,
                     created_at=now, updated_at=now)
    ss.next_run_at = sched.first_run(ss, now)
    s.add(ss)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        raise HTTPException(409, "Ya tienes una búsqueda programada con ese nombre")
    s.refresh(ss)
    return _out(ss)


@router.get("/{sid}")
def get_one(sid: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    return _out(_own(s, user, sid))


@router.patch("/{sid}")
def patch(sid: int, body: SavedSearchPatch, user: User = Depends(current_user), s: Session = Depends(get_session)):
    ss = _own(s, user, sid)
    data = body.model_dump(exclude_unset=True)
    try:
        if any(k in data for k in sched.PARAM_KEYS):
            params = dict(ss.params or {})
            for k in sched.PARAM_KEYS:
                if k in data:
                    if data[k] is None:
                        params.pop(k, None)          # null = volver al valor de las preferencias
                    else:
                        params[k] = data[k]
            ss.params = sched.clean_params(params)
        if "name" in data and data["name"]:
            ss.name = data["name"].strip()
        if "interval_minutes" in data and data["interval_minutes"] is not None:
            ss.interval_minutes = sched.check_interval(data["interval_minutes"])
        for k in ("timezone", "window_start", "window_end"):
            if k in data:
                setattr(ss, k, data[k] if k != "timezone" else (data[k] or ss.timezone))
        if "window_days" in data:
            ss.window_days = sorted(set(data["window_days"] or []))
        sched.check_window(ss.timezone, ss.window_start, ss.window_end, ss.window_days)
    except ValueError as e:
        raise _bad(e)
    now = utcnow()
    reactivated = data.get("active") is True and not ss.active
    if "active" in data and data["active"] is not None:
        ss.active = data["active"]
        if reactivated:
            ss.failure_streak, ss.paused_reason, ss.last_error = 0, None, None
        elif not ss.active:
            ss.paused_reason = "pausada por el usuario"
    if reactivated:
        ss.next_run_at = sched.first_run(ss, now)
    elif any(k in data for k in ("interval_minutes", "timezone", "window_start", "window_end", "window_days")) and ss.active:
        ss.next_run_at = sched.next_run(ss, windows.aware(ss.last_run_at)) if ss.last_run_at else sched.first_run(ss, now)
    ss.updated_at = now
    s.add(ss)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        raise HTTPException(409, "Ya tienes una búsqueda programada con ese nombre")
    s.refresh(ss)
    return _out(ss)


@router.post("/{sid}/pause")
def pause(sid: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    return patch(sid, SavedSearchPatch(active=False), user, s)


@router.post("/{sid}/resume")
def resume(sid: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    return patch(sid, SavedSearchPatch(active=True), user, s)


@router.post("/{sid}/run", status_code=202)
def run_now(sid: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    """Pide una ejecución ahora: el trabajador la toma en su próxima vuelta. Respeta la cortesía: no más de una cada 5 minutos."""
    ss = _own(s, user, sid)
    if not ss.active:
        raise HTTPException(409, "La búsqueda está pausada: reactívala primero")
    now = utcnow()
    if ss.last_run_at and windows.aware(ss.last_run_at) > now - timedelta(minutes=5):
        raise HTTPException(429, "Ya se ejecutó hace menos de 5 minutos", headers={"Retry-After": "300"})
    ss.next_run_at = now
    s.add(ss)
    s.commit()
    s.refresh(ss)
    return _out(ss)


@router.delete("/{sid}", status_code=204)
def delete(sid: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    s.delete(_own(s, user, sid))
    s.commit()
    return Response(status_code=204)
