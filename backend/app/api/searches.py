from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app.api.deps import current_user, load_profile
from app.db import SearchRun, User, get_session
from app.services.runner import start_search

router = APIRouter(prefix="/me/searches", tags=["searches"])


class SearchIn(BaseModel):
    keywords: list[str] | None = None            # si no, se usan los roles objetivo de las preferencias
    limit_per_source: int = Field(25, ge=1, le=100)
    # filtros de ESTA búsqueda (si no vienen, se usan las preferencias del usuario); permiten buscar un rol distinto al del CV
    countries: list[str] | None = Field(None, max_length=10)
    remote: bool | None = None
    work_modes: list[str] | None = Field(None, max_length=3)            # remote | hybrid | onsite
    employment_types: list[str] | None = Field(None, max_length=5)      # full_time | part_time | contract | internship | temporary
    hours_old: int | None = Field(None, ge=1, le=24 * 90)               # antigüedad máxima de la publicación


def _out(r: SearchRun) -> dict:
    return {"id": r.id, "status": r.status, "sources": r.sources, "new_jobs": r.new_jobs, "error": r.error,
            "keywords": (r.params or {}).get("keywords_used") or (r.params or {}).get("keywords") or [],
            "created_at": r.created_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None}


@router.post("", status_code=202)
def create(body: SearchIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    profile = load_profile(session, user)
    bad = [m for m in (body.work_modes or []) if m not in ("remote", "hybrid", "onsite")] + [t for t in (body.employment_types or []) if t not in ("full_time", "part_time", "contract", "internship", "temporary")]
    if bad:
        raise HTTPException(422, f"Valores no válidos: {', '.join(bad)}")
    if not (body.keywords or profile.preferences.target_roles or profile.headline):
        raise HTTPException(422, "Define roles objetivo en tus preferencias")
    active = session.exec(select(SearchRun).where(SearchRun.user_id == user.id, col(SearchRun.status).in_(["queued", "running"]))).first()
    if active:
        raise HTTPException(409, f"Ya hay una búsqueda en curso (#{active.id})")
    return _out(start_search(session, user.id, body.model_dump(exclude_none=True)))


@router.get("")
def list_runs(user: User = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.exec(select(SearchRun).where(SearchRun.user_id == user.id).order_by(col(SearchRun.id).desc()).limit(20)).all()
    return [_out(r) for r in rows]


@router.get("/{run_id}")
def get_run(run_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    r = session.get(SearchRun, run_id)
    if not r or r.user_id != user.id:
        raise HTTPException(404, "Búsqueda no encontrada")
    return _out(r)
