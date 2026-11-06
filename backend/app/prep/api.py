"""REST de la preparación (/api/v1/me/...): lanza la tarea en segundo plano y lee lo calculado."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import current_user, get_llm, get_writer
from app.api.jobs import _own
from app.db import User, get_session
from app.prep import builders as b
from app.prep import service
from app.services.tasks import start_task

router = APIRouter(prefix="/me", tags=["prep"])


class PrepIn(BaseModel):
    parts: list[Literal["stories", "questions", "ask", "negotiation", "upskill"]] | None = None
    lang: Literal["es", "en"] | None = None            # por defecto, el idioma de la oferta
    force: bool = False                                # ignorar la caché
    use_llm: bool = False                              # preguntas extra con el modelo (lento en CPU; apagado por defecto)
    salary_min: float | None = Field(None, ge=0)
    salary_max: float | None = Field(None, ge=0)
    salary_currency: str | None = Field(None, max_length=8)


@router.post("/jobs/{job_id}/prep", status_code=202)
def start_prep(job_id: int, body: PrepIn, user: User = Depends(current_user), session: Session = Depends(get_session),
               llm=Depends(get_llm), writer=Depends(get_writer)):
    """Lanza la preparación como tarea en segundo plano (consulta /me/tasks/{id}); el resultado se lee con GET /me/jobs/{id}/prep."""
    _own(session, user, job_id)
    if body.salary_min and body.salary_max and body.salary_min > body.salary_max:
        raise HTTPException(422, "el mínimo salarial no puede superar al máximo")
    if body.salary_min or body.salary_max:                       # el rango queda guardado en su candidatura para la próxima vez
        from app.tracking import service as tr
        job = _own(session, user, job_id)
        app = tr.ensure_application(session, user, job)
        app.expected_salary_min, app.expected_salary_max, app.expected_salary_currency = body.salary_min, body.salary_max, body.salary_currency
        session.add(app)
        session.commit()
    t = start_task(session, user, job_id, "prep", body.model_dump(exclude_none=True), llm, writer)
    return {"id": t.id, "job_id": t.job_id, "kind": t.kind, "status": t.status}


@router.get("/jobs/{job_id}/prep")
def get_prep(job_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Lo ya preparado para la oferta. `stale` indica que el perfil, la oferta o las brechas cambiaron desde entonces (relánzala con force)."""
    row = _own(session, user, job_id)
    cached = service.get_cached(session, user, job_id)
    stale: dict[str, bool] = {}
    if cached and row.gaps is not None:
        from app.api.deps import load_profile
        profile = load_profile(session, user)
        lang = next(iter(cached.values())).lang
        sal = service.salary_of(session, user, job_id, {})
        use_llm = any(q.get("origin") == "llm" for q in cached.get("questions").data.get("questions", [])) if "questions" in cached else False
        cur = service.input_hashes(profile, row, row.gaps, lang, service.analyzed_jobs(session, user), sal, use_llm)
        stale = {k: cached[k].input_hash != cur[k] for k in cached}
    return {"job_id": job_id, "ready": sorted(cached), "stale": stale,
            **{k: {"lang": r.lang, "generated_at": r.created_at.isoformat(), **r.data} for k, r in cached.items()}}


@router.get("/prep/upskill")
def get_upskill(lang: Literal["es", "en"] = "es", limit: int = Query(15, ge=1, le=50), user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Plan de mejora global: brechas ordenadas por frecuencia en TODAS tus ofertas analizadas (determinista, sin LLM)."""
    from app.api.deps import load_profile
    return b.build_upskill(load_profile(session, user), service.analyzed_jobs(session, user), lang, limit)



