from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from app.ai.access import reason_message
from app.ai.report import AiRun, ai_from_trace, disabled_reason, usable
from app.api.deps import current_user, get_llm, get_writer, load_profile
from app.ats.gaps import classify, extract_requirements, match_score
from app.cv.artifact import verify_artifact
from app.cv.cover_letter import generate_cover_letter
from app.cv.finalize import finalize_cv
from app.cv.fit import fit_cv, fit_letter
from app.cv.pdf import render_pdf
from app.cv.render import render_docx
from app.cv.tailor import tailor_cv
from app.cv.templates import DEFAULT_TEMPLATE, TEMPLATES
from app.db import JobEvent, JobRow, TailoredRow, User, get_session
from app.domain.job import Job
from app.domain.profile import Skill, SkillSource
from app.domain.tailoring import KeywordGap, TailoredCV, includable
from app.evaluation import service as evaluation_service
from app.evaluation.models import Evaluation
from app.llm.client import LLMClient, LLMDisabled, LLMError
from app.rag.service import retriever_for
from app.text import clean_title
from app.tracking import service as tracking

router = APIRouter(prefix="/me", tags=["jobs"])


Recommendation = Literal["apply", "consider", "skip"]


class JobStatus(str, Enum):
    NEW = "new"
    SAVED = "saved"
    DISCARDED = "discarded"
    APPLIED = "applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"


class JobOut(BaseModel):
    id: int
    job: Job
    status: str
    stage1: float | None
    match: int | None
    gaps: list[KeywordGap] | None
    first_seen: str
    evaluation: Evaluation | None = None     # informe guardado (ver GET /me/jobs/{id}/evaluation)
    ai: dict | None = None                   # {ai_used, ai_reason, ai_message, ai_mode}: solo en la respuesta que calcula el análisis


class JobPage(BaseModel):
    total: int
    items: list[JobOut]


def _out(r: JobRow, ai: dict | None = None) -> JobOut:
    return JobOut(ai=ai, id=r.id, job=Job.model_validate(r.data), status=r.status, stage1=r.stage1, match=r.match,
                  gaps=[KeywordGap.model_validate(g) for g in r.gaps] if r.gaps is not None else None,
                  first_seen=r.first_seen.isoformat(), evaluation=evaluation_service.stored(r))


def _own(session: Session, user: User, job_id: int) -> JobRow:
    row = session.get(JobRow, job_id)
    if not row or row.user_id != user.id:     # 404 y no 403: no revelar que existe
        raise HTTPException(404, "Oferta no encontrada")
    return row


def _why(e: Exception, *llms) -> str:
    """Motivo de la degradación a la ruta sin IA cuando el LLM falla a mitad de una operación."""
    if isinstance(e, LLMDisabled):
        return e.reason
    return next((x.access.reason for x in llms if getattr(getattr(x, "access", None), "enabled", True) is False and x.access.reason), "llm_unavailable")


def _det_corpus(session: Session, user: User, exclude_id: int | None = None, limit: int = 150) -> list[str]:
    """Descripciones de las demás ofertas del usuario: corpus local para el TF-IDF del extractor determinista."""
    rows = session.exec(select(JobRow.data).where(JobRow.user_id == user.id, JobRow.id != exclude_id).limit(limit)).all()
    return [f"{d.get('title', '')} {d.get('description', '')}" for d in rows if isinstance(d, dict)]


def _gaps_for(session: Session, user: User, row: JobRow, profile, job: Job, llm, run: AiRun, confirmed: set[str]) -> list[KeywordGap]:
    """Requisitos + brechas. Con IA si está disponible; si el LLM falla o se agota el presupuesto, ruta determinista (nunca un 502)."""
    def build(model):
        corpus = _det_corpus(session, user, row.id) if usable(model) is None else None
        return classify(extract_requirements(job, model, profile, corpus), profile, model, confirmed, clean_title(job.title),
                        retriever=retriever_for(session, user.id, profile))
    if usable(llm) is not None:
        try:
            return build(llm)
        except LLMError as e:
            run.fell_back(_why(e))
    return build(None)


def _event(session: Session, user: User, job_id: int, kind: str, note: str | None = None) -> None:
    session.add(JobEvent(user_id=user.id, job_id=job_id, kind=kind, note=note))


def _haystack(r: JobRow) -> str:
    d = r.data
    return f"{d.get('title', '')} {d.get('company', '')} {d.get('description', '')[:4000]}".lower()


def _posted(r: JobRow) -> datetime:
    """Fecha de publicación (o, si la oferta no la dice, cuándo la vimos por primera vez), siempre con zona UTC."""
    raw = r.data.get("posted_at")
    if raw:
        try:
            d = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    f = r.first_seen
    return f if f.tzinfo else f.replace(tzinfo=timezone.utc)


def _annual_max(r: JobRow) -> float | None:
    d = r.data
    vals = [v for v in (d.get("salary_annual_max"), d.get("salary_annual_min")) if v]
    return max(vals) if vals else None


def _apply_filters(rows, work_mode, employment_type, min_salary, salary_currency, with_salary, posted_days, country, source):
    if work_mode:
        rows = [r for r in rows if (r.data.get("work_mode") or ("remote" if r.data.get("remote") else None)) == work_mode]
    if employment_type:
        rows = [r for r in rows if r.data.get("employment_type") == employment_type]
    if min_salary is not None or with_salary:
        rows = [r for r in rows if _annual_max(r) is not None]
        if min_salary is not None:
            rows = [r for r in rows if _annual_max(r) >= min_salary]
    if salary_currency:
        rows = [r for r in rows if (r.data.get("salary_currency") or "").upper() == salary_currency.upper()]
    if posted_days:
        limit = datetime.now(timezone.utc) - timedelta(days=posted_days)
        rows = [r for r in rows if _posted(r) >= limit]
    if country:
        rows = [r for r in rows if (r.data.get("country") or "").upper() == country.upper()]
    if source:
        rows = [r for r in rows if str(r.data.get("source", "")).split(":")[0] == source]
    return rows


@router.get("/jobs", response_model=JobPage)
def list_jobs(status: JobStatus | None = None, min_match: int | None = Query(None, ge=0, le=100),
              min_stage1: float | None = Query(None, ge=0, le=100), q: str | None = None,
              limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
              recommendation: Recommendation | None = None, sort: str = Query("match", pattern="^(match|score|recent|salary)$"),
              work_mode: str | None = Query(None, pattern="^(remote|hybrid|onsite)$"),
              employment_type: str | None = Query(None, pattern="^(full_time|part_time|contract|internship|temporary)$"),
              min_salary: float | None = Query(None, ge=0, description="salario anual mínimo; solo ofertas que publican salario y llegan a ese monto"),
              salary_currency: str | None = Query(None, max_length=8), with_salary: bool = False,
              posted_days: int | None = Query(None, ge=1, le=365), country: str | None = Query(None, max_length=2),
              source: str | None = Query(None, max_length=40),
              user: User = Depends(current_user), session: Session = Depends(get_session)):
    stmt = select(JobRow).where(JobRow.user_id == user.id)
    if status:
        stmt = stmt.where(JobRow.status == status.value)
    else:
        stmt = stmt.where(JobRow.status != JobStatus.DISCARDED.value)
    if min_match is not None:
        stmt = stmt.where(col(JobRow.match) >= min_match)
    if min_stage1 is not None:
        stmt = stmt.where(col(JobRow.stage1) >= min_stage1)
    rows = session.exec(stmt).all()
    if q:                                      # cada palabra debe aparecer en el cargo, la empresa o la descripción (rol, especialidad o tecnología)
        terms = [t for t in q.lower().split() if t]
        rows = [r for r in rows if all(t in _haystack(r) for t in terms)]
    rows = _apply_filters(rows, work_mode, employment_type, min_salary, salary_currency, with_salary, posted_days, country, source)
    if recommendation or sort == "score":      # evaluación determinista (sin LLM): se calcula y guarda lo que falte o esté desactualizado
        evaluation_service.evaluate_many(session, rows, load_profile(session, user))
        if recommendation:
            rows = [r for r in rows if (r.evaluation or {}).get("recommendation") == recommendation]
    if sort == "score":
        rows.sort(key=lambda r: ((r.evaluation or {}).get("score", 0), r.match or 0), reverse=True)
    elif sort == "recent":
        rows.sort(key=_posted, reverse=True)
    elif sort == "salary":
        rows.sort(key=lambda r: (_annual_max(r) or -1, _posted(r)), reverse=True)
    else:
        rows.sort(key=lambda r: (r.match if r.match is not None else -1, r.stage1 or 0), reverse=True)
    return JobPage(total=len(rows), items=[_out(r) for r in rows[offset: offset + limit]])


@router.get("/jobs/{job_id}/evaluation", response_model=Evaluation)
def job_evaluation(job_id: int, force: bool = False, lang: str | None = Query(None, pattern="^(es|en)$"),
                   user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Informe de evaluación A-H accionable: puntaje 1-5, recomendación apply|consider|skip, dimensiones, vetos y banderas.
    Determinista y sin LLM; se guarda en JobRow.evaluation y se reutiliza mientras no cambien perfil, preferencias, análisis o fecha."""
    row = _own(session, user, job_id)
    return evaluation_service.evaluate_row(session, row, load_profile(session, user), lang=lang, force=force)


@router.post("/evaluations", status_code=202)
def evaluate_batch(force: bool = False, user: User = Depends(current_user), session: Session = Depends(get_session),
                   llm: LLMClient = Depends(get_llm), writer: LLMClient = Depends(get_writer)):
    """Evalúa en segundo plano todas las ofertas activas (tarea 'evaluate'); consulta el avance en /me/tasks."""
    from app.services.tasks import start_task
    first = session.exec(select(JobRow).where(JobRow.user_id == user.id, JobRow.status != JobStatus.DISCARDED.value).order_by(col(JobRow.id))).first()
    if first is None:
        raise HTTPException(404, "No hay ofertas que evaluar")
    t = start_task(session, user, first.id, "evaluate", {"batch": True, "force": force}, llm, writer)
    return {"id": t.id, "job_id": t.job_id, "kind": t.kind, "status": t.status}


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return _out(_own(session, user, job_id))


class StatusIn(BaseModel):
    status: JobStatus


@router.patch("/jobs/{job_id}", response_model=JobOut)
def set_status(job_id: int, body: StatusIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    row = _own(session, user, job_id)
    row.status = body.status.value
    session.add(row)
    _event(session, user, row.id, f"status:{body.status.value}")
    tracking.on_job_status(session, user, row, body.status.value)    # 'applied' archiva lo enviado; el resto mantiene coherente el pipeline
    session.commit()
    return _out(row)


@router.get("/jobs/{job_id}/events")
def job_events(job_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    _own(session, user, job_id)
    evs = session.exec(select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.ts)).all()
    return [{"kind": e.kind, "note": e.note, "ts": e.ts.isoformat()} for e in evs]


@router.post("/jobs/{job_id}/analyze", response_model=JobOut)
def analyze(job_id: int, force: bool = False, user: User = Depends(current_user),
            session: Session = Depends(get_session), llm: LLMClient = Depends(get_llm)):
    """Qué pide la oferta vs qué tienes. Se cachea. Sin IA (modo none, sin consentimiento, presupuesto agotado o LLM caído)
    usa el extractor determinista y la clasificación por reglas; `ai` en la respuesta dice si se usó IA y por qué no."""
    row = _own(session, user, job_id)
    if row.gaps is not None and not force:
        return _out(row)
    profile = load_profile(session, user)
    job = Job.model_validate(row.data)
    run = AiRun(llm)
    confirmed = {s.name for s in profile.skills if s.source == SkillSource.USER_CONFIRMED}
    gaps = _gaps_for(session, user, row, profile, job, llm, run, confirmed)
    row.gaps, row.match = [g.model_dump() for g in gaps], match_score(gaps)
    session.add(row)
    _event(session, user, row.id, "analyzed", f"match={row.match}")
    session.commit()
    return _out(row, run.report())


class TailorIn(BaseModel):
    confirmed: list[str] = []          # requisitos 'missing' que el usuario confirma tener
    save_confirmed: bool = True        # recordarlos en su perfil


class TailoredOut(BaseModel):
    id: int
    job_id: int
    cv: TailoredCV
    created_at: str
    ai: dict | None = None             # {ai_used, ai_reason, ai_message, ai_mode}: si el CV se redactó con IA o de forma extractiva


@router.post("/jobs/{job_id}/tailor", response_model=TailoredOut)
def tailor(job_id: int, body: TailorIn, user: User = Depends(current_user), session: Session = Depends(get_session),
           llm: LLMClient = Depends(get_llm), writer: LLMClient = Depends(get_writer)):
    row = _own(session, user, job_id)
    profile = load_profile(session, user)
    job = Job.model_validate(row.data)
    confirmed_now = [c.strip() for c in body.confirmed if c.strip()]
    run = AiRun(llm, writer)
    confirmed = {s.name for s in profile.skills if s.source == SkillSource.USER_CONFIRMED} | set(confirmed_now)
    gaps = _gaps_for(session, user, row, profile, job, llm, run, confirmed)
    if body.save_confirmed and confirmed_now:
        have = profile.skill_names()
        profile.skills += [Skill(name=c, source=SkillSource.USER_CONFIRMED) for c in confirmed_now if c.lower() not in have]
        from app.api.profile import _save
        _save(session, user, profile)

    def write(w, reason: str | None = None):
        cv_ = tailor_cv(profile, job, gaps, w, no_ai_reason=reason)
        return finalize_cv(cv_, job, gaps, w, profile=profile)     # idioma de la oferta + reporte ATS final

    try:
        cv = write(writer)
    except (LLMError, RuntimeError) as e:
        if isinstance(e, RuntimeError) and not isinstance(e, LLMError) and "No se pudo generar" not in str(e):
            raise HTTPException(502, str(e))
        run.fell_back(_why(e, llm, writer))                          # el redactor cayó o se agotó el presupuesto: CV extractivo, no un 502
        cv = write(None, run.fallback)
    ai = run.report()
    if not ai_from_trace(cv.trace)["ai_used"]:
        ai = {**ai, **ai_from_trace(cv.trace)}
    t = TailoredRow(user_id=user.id, job_id=row.id, data=cv.model_dump(mode="json"), confirmed=confirmed_now, ats_score=cv.ats.score if cv.ats else 0)
    row.gaps, row.match = [g.model_dump() for g in gaps], match_score(gaps)
    session.add_all([t, row])
    _event(session, user, row.id, "tailored", f"ats={t.ats_score}")
    session.commit()
    session.refresh(t)
    return TailoredOut(id=t.id, job_id=row.id, cv=cv, created_at=t.created_at.isoformat(), ai=ai)


@router.get("/jobs/{job_id}/cvs", response_model=list[TailoredOut])
def list_cvs(job_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    _own(session, user, job_id)
    rows = session.exec(select(TailoredRow).where(TailoredRow.job_id == job_id).order_by(col(TailoredRow.created_at).desc())).all()
    return [TailoredOut(id=t.id, job_id=t.job_id, cv=TailoredCV.model_validate(t.data), created_at=t.created_at.isoformat(),
                        ai=ai_from_trace(t.data.get("trace", []))) for t in rows]


def _own_cv(session: Session, user: User, cv_id: int, template: str) -> tuple[TailoredRow, TailoredCV]:
    t = session.get(TailoredRow, cv_id)
    if not t or t.user_id != user.id:
        raise HTTPException(404, "CV no encontrado")
    if template not in TEMPLATES:
        raise HTTPException(422, f"Plantilla desconocida: usa {', '.join(TEMPLATES)}")
    return t, TailoredCV.model_validate(t.data)


def _gaps_of(session: Session, t: TailoredRow) -> list[KeywordGap]:
    job = session.get(JobRow, t.job_id)
    return [KeywordGap.model_validate(g) for g in (job.gaps or [])] if job else []


def _fitted(session: Session, t: TailoredRow, cv: TailoredCV, template: str, pages: int | None) -> tuple[TailoredCV, dict]:
    """Con `pages`, recorta por relevancia hasta que quepa (el PDF/DOCX se mide con reportlab). Sin `pages`, el CV tal cual."""
    if not pages:
        return cv, {}
    out, rep = fit_cv(cv, pages, template, _gaps_of(session, t))
    return out, {"X-CV-Pages": str(rep.pages_after), "X-CV-Trimmed": str(len(rep.trimmed)), "X-CV-Fits": str(rep.fits).lower()}


@router.get("/cvs/{cv_id}.docx")
def download_cv(cv_id: int, template: str = Query(DEFAULT_TEMPLATE, description="harvard | classic | compact | modern"),
                pages: int | None = Query(None, ge=1, le=2, description="Ajustar a 1 o 2 páginas recortando por relevancia"),
                user: User = Depends(current_user), session: Session = Depends(get_session)):
    t, cv = _own_cv(session, user, cv_id, template)
    cv, hdr = _fitted(session, t, cv, template, pages)
    return Response(render_docx(cv, template),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="cv-{cv_id}.docx"', **hdr})


@router.get("/cvs/{cv_id}.pdf")
def download_cv_pdf(cv_id: int, template: str = Query(DEFAULT_TEMPLATE, description="harvard | classic | compact | modern"),
                    pages: int | None = Query(None, ge=1, le=2, description="Ajustar a 1 o 2 páginas recortando por relevancia"),
                    user: User = Depends(current_user), session: Session = Depends(get_session)):
    """PDF ATS-friendly: una columna, fuentes estándar y texto seleccionable."""
    t, cv = _own_cv(session, user, cv_id, template)
    cv, hdr = _fitted(session, t, cv, template, pages)
    return Response(render_pdf(cv, template), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="cv-{cv_id}.pdf"', **hdr})


@router.get("/cvs/{cv_id}/fit")
def cv_fit_report(cv_id: int, template: str = Query(DEFAULT_TEMPLATE), pages: int = Query(2, ge=1, le=2), user: User = Depends(current_user),
                  session: Session = Depends(get_session)):
    """Cuántas páginas ocupa (medido) y qué se recortaría para llegar a `pages`, con el motivo de cada recorte. No modifica nada."""
    t, cv = _own_cv(session, user, cv_id, template)
    return fit_cv(cv, pages, template, _gaps_of(session, t))[1].compact()


@router.get("/cvs/{cv_id}/artifact")
def cv_artifact_report(cv_id: int, template: str = Query(DEFAULT_TEMPLATE), format: str = Query("pdf", pattern="^(pdf|docx)$"),
                       pages: int | None = Query(None, ge=1, le=2), user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Puntaje ATS del ARCHIVO real (capa de texto extraída del PDF/DOCX) frente al texto lógico, y qué se pierde, si algo."""
    t, cv = _own_cv(session, user, cv_id, template)
    cv, _ = _fitted(session, t, cv, template, pages)
    return verify_artifact(cv, _gaps_of(session, t), template, format).model_dump(mode="json")


class CoverLetterIn(BaseModel):
    confirmed: list[str] = []          # requisitos 'missing' que el usuario confirma tener
    lang: str | None = None            # 'es' | 'en'; por defecto el idioma de la oferta


@router.post("/jobs/{job_id}/cover-letter")
def cover_letter(job_id: int, body: CoverLetterIn, user: User = Depends(current_user), session: Session = Depends(get_session),
                 llm: LLMClient = Depends(get_llm), writer: LLMClient = Depends(get_writer)):
    """Carta de presentación con las mismas guardias anti-invención que el CV (nada que el usuario no tenga o confirme)."""
    row = _own(session, user, job_id)
    profile = load_profile(session, user)
    job = Job.model_validate(row.data)
    run = AiRun(llm, writer)
    if row.gaps is not None:
        gaps = [KeywordGap.model_validate(g) for g in row.gaps]
    else:
        confirmed = {s.name for s in profile.skills if s.source == SkillSource.USER_CONFIRMED}
        gaps = _gaps_for(session, user, row, profile, job, llm, run, confirmed)
    ok = {c.strip().lower() for c in body.confirmed if c.strip()} | {s.name.lower() for s in profile.skills if s.source == SkillSource.USER_CONFIRMED}
    for g in gaps:
        if g.keyword.lower() in ok:
            g.confirmed = True
    letter = generate_cover_letter(profile, job, gaps, writer, body.lang)
    ai = run.report()
    if letter.source == "llm":
        ai = {**ai, "ai_used": True, "ai_reason": None, "ai_message": None}
    elif usable(writer) is not None:                          # el modelo respondió pero no pasó las guardias: plantilla
        ai = {**ai, "ai_used": False, "ai_reason": "validation_failed", "ai_message": reason_message("validation_failed")}
    else:
        ai = {**ai, "ai_used": False, "ai_reason": ai["ai_reason"] or disabled_reason(writer), "ai_message": reason_message(ai["ai_reason"] or disabled_reason(writer))}
    _event(session, user, row.id, "cover_letter", letter.source)
    session.commit()
    # la carta debe caber en 1 página (medido con reportlab)
    text, fit = fit_letter(letter.text, [g.keyword for g in gaps if includable(g)])
    return {**letter.model_dump(), "text": text, "fit": fit.compact(), "ai": ai}


@router.post("/jobs/{job_id}/apply")
def open_apply(job_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """El botón 'Aplicar' del panel. El sistema NO envía nada: registra la intención y entrega el destino."""
    row = _own(session, user, job_id)
    job = Job.model_validate(row.data)
    _event(session, user, row.id, "apply_opened", job.apply_url or job.url)
    session.commit()
    return {"url": job.apply_url or job.url, "external": bool(job.apply_url) and job.apply_url != job.url}


@router.get("/stats")
def stats(user: User = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.exec(select(JobRow.status, func.count()).where(JobRow.user_id == user.id).group_by(JobRow.status)).all()
    return {"by_status": {s: n for s, n in rows}}
