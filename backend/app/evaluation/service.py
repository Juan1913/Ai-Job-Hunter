"""Persistencia y lotes de la evaluación: calcula, guarda en JobRow.evaluation y reutiliza el informe mientras sus entradas no cambien."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

from sqlmodel import Session, col, select

from app.ai.report import disabled_reason, usable
from app.db import JobRow, User
from app.domain.job import Job
from app.domain.profile import Profile
from app.evaluation import engine
from app.evaluation.models import Evaluation, Requirements


def user_lang(profile: Profile, lang: str | None = None) -> str:
    """Idioma del informe: el pedido; si no, inglés solo si el usuario busca únicamente en inglés; por defecto español."""
    if lang in ("es", "en"):
        return lang
    langs = [x.lower()[:2] for x in profile.preferences.languages]
    return "en" if langs == ["en"] else "es"


def stored(row: JobRow) -> Evaluation | None:
    try:
        return Evaluation.model_validate(row.evaluation) if row.evaluation else None
    except Exception:   # noqa: BLE001  informe de una versión anterior del esquema: se recalcula
        return None


def evaluate_row(session: Session, row: JobRow, profile: Profile, *, lang: str | None = None, force: bool = False, llm=None,
                 today: date | None = None, commit: bool = True) -> Evaluation:
    """Devuelve el informe vigente de la oferta, recalculándolo si cambió algo (perfil, preferencias, análisis, fecha) o `force`."""
    reason = disabled_reason(llm) if llm is not None else "not_needed"
    llm = usable(llm)
    lang = user_lang(profile, lang)
    today = today or datetime.now(timezone.utc).date()
    job = Job.model_validate(row.data)
    prev = stored(row)
    fp = engine.fingerprint(job, profile, row.stage1, row.match, row.gaps, today, lang)
    if prev and prev.fingerprint == fp and not force and not (llm is not None and prev.requirements and engine.needs_llm(job, prev.requirements)
                                                               and prev.requirements.source != "llm"):
        prev.cached = True
        return prev
    cached_req = prev.requirements if prev and prev.requirements and prev.requirements.source == "llm" and prev.requirements.fingerprint == \
        hashlib.sha1((job.title + job.description).encode()).hexdigest()[:16] else None
    ev = engine.evaluate(job, profile, stage1=row.stage1, match=row.match, gaps=row.gaps, today=today, lang=lang, job_id=row.id, cached_req=cached_req)
    if llm is not None and ev.requirements and cached_req is None and engine.needs_llm(job, ev.requirements):
        req = engine.refine_requirements(job, ev.requirements, llm)       # una llamada, y queda guardada dentro del informe
        ev = engine.evaluate(job, profile, stage1=row.stage1, match=row.match, gaps=row.gaps, today=today, lang=lang, job_id=row.id, cached_req=req)
        ev.requirements = req if req.source == "llm" else ev.requirements
    ev.ai_used = bool(ev.requirements and ev.requirements.source == "llm")
    ev.ai_reason = None if ev.ai_used else (reason or "not_needed")
    row.evaluation = ev.model_dump(mode="json")
    session.add(row)
    if commit:
        session.commit()
    return ev


def evaluate_many(session: Session, rows: list[JobRow], profile: Profile, **kw) -> dict[int, Evaluation]:
    out = {r.id: evaluate_row(session, r, profile, commit=False, **kw) for r in rows}
    session.commit()
    return out


def evaluate_all(session: Session, user: User, profile: Profile, *, llm=None, force: bool = False, lang: str | None = None) -> dict:
    """Lote: todas las ofertas activas (no descartadas) del usuario. Devuelve conteos por recomendación."""
    rows = session.exec(select(JobRow).where(JobRow.user_id == user.id, col(JobRow.status) != "discarded")).all()
    counts = {"apply": 0, "consider": 0, "skip": 0}
    for r in rows:
        counts[evaluate_row(session, r, profile, llm=llm, force=force, lang=lang, commit=False).recommendation] += 1
    session.commit()
    return {"evaluated": len(rows), **counts}


__all__ = ["Requirements", "evaluate_all", "evaluate_many", "evaluate_row", "user_lang"]
