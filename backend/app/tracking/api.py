"""REST del seguimiento (/api/v1/me/...). Todo es por usuario: una oferta, candidatura o archivo ajeno responde 404, nunca 403."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app.api.deps import current_user, get_llm
from app.api.jobs import _own
from app.db import ApplicationArchive, ApplicationRow, JobRow, User, get_session
from app.tracking import service as svc

router = APIRouter(prefix="/me", tags=["tracking"])


def _app(session: Session, user: User, job_id: int) -> tuple[JobRow, ApplicationRow]:
    job = _own(session, user, job_id)
    app = svc.get_application(session, user, job_id)
    if app is None:
        raise HTTPException(404, "Esta oferta aún no tiene seguimiento")
    return job, app


def _bad(e: Exception) -> HTTPException:
    return HTTPException(422, str(e))


def _gone(what: str) -> HTTPException:
    return HTTPException(404, f"{what} no encontrado")


class ApplyIn(BaseModel):
    applied_at: datetime | None = None            # por defecto, ahora
    tailored_id: int | None = None                # CV exacto usado (por defecto, el último generado para la oferta)
    cover_letter: str | None = Field(None, max_length=20000)
    template: str | None = Field(None, max_length=40)
    next_step: str | None = Field(None, max_length=300)
    next_step_at: datetime | None = None
    note: str | None = Field(None, max_length=4000)


class ApplicationPatch(BaseModel):
    """Solo los campos enviados se modifican (enviar null lo borra)."""
    stage: Literal["saved", "applied", "screening", "interviewing", "offer", "closed"] | None = None
    tailored_id: int | None = None
    template: str | None = Field(None, max_length=40)
    cover_letter: str | None = Field(None, max_length=20000)
    applied_at: datetime | None = None
    next_step: str | None = Field(None, max_length=300)
    next_step_at: datetime | None = None
    deadline_at: datetime | None = None
    first_response_at: datetime | None = None
    last_contact_at: datetime | None = None
    snoozed_until: datetime | None = None
    notes: str | None = Field(None, max_length=8000)
    expected_salary_min: float | None = Field(None, ge=0)
    expected_salary_max: float | None = Field(None, ge=0)
    expected_salary_currency: str | None = Field(None, max_length=8)


class InterviewIn(BaseModel):
    kind: str = "other"
    scheduled_at: datetime | None = None
    done: bool = False
    interviewer: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=8000)
    result: str | None = Field(None, max_length=500)


class InterviewPatch(BaseModel):
    kind: str | None = None
    scheduled_at: datetime | None = None
    done: bool | None = None
    interviewer: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=8000)
    result: str | None = Field(None, max_length=500)


class ContactIn(BaseModel):
    name: str = Field(max_length=200)
    role: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=60)
    notes: str | None = Field(None, max_length=4000)


class ContactPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    role: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=60)
    notes: str | None = Field(None, max_length=4000)


class OutcomeIn(BaseModel):
    outcome: Literal["offer", "rejected", "no_response", "withdrawn"] | None      # null reabre la candidatura
    reason: str | None = Field(None, max_length=1000)
    at: datetime | None = None


class SnoozeIn(BaseModel):
    until: datetime


# ----------------------------------------------------------------------- candidatura de una oferta

@router.post("/jobs/{job_id}/application/apply", status_code=201)
def apply(job_id: int, body: ApplyIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """El USUARIO declara que aplicó (por su cuenta: el sistema no envía nada). Vincula el CV/carta exactos y archiva la oferta."""
    job = _own(session, user, job_id)
    try:
        app, arc = svc.mark_applied(session, user, job, **body.model_dump())
    except svc.TrackingError as e:
        raise _bad(e)
    session.commit()
    session.refresh(app)
    return {**svc.application_out(session, app), "archive_id": arc.id if arc else None}


@router.put("/jobs/{job_id}/application")
def start_tracking(job_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Empieza a seguir una oferta sin haber aplicado todavía (fecha límite, notas). Idempotente."""
    job = _own(session, user, job_id)
    app = svc.ensure_application(session, user, job)
    session.commit()
    session.refresh(app)
    return svc.application_out(session, app)


@router.get("/jobs/{job_id}/application")
def get_application(job_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    _, app = _app(session, user, job_id)
    return svc.application_out(session, app)


@router.patch("/jobs/{job_id}/application")
def patch_application(job_id: int, body: ApplicationPatch, user: User = Depends(current_user), session: Session = Depends(get_session)):
    job = _own(session, user, job_id)
    app = svc.ensure_application(session, user, job)
    try:
        svc.update_fields(session, user, job, app, body.model_dump(exclude_unset=True))
    except svc.TrackingError as e:
        raise _bad(e)
    session.commit()
    session.refresh(app)
    return svc.application_out(session, app)


@router.put("/jobs/{job_id}/application/outcome")
def put_outcome(job_id: int, body: OutcomeIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Resultado final: oferta / rechazo / sin respuesta / retirada, con su motivo. `outcome: null` reabre la candidatura."""
    job = _own(session, user, job_id)
    app = svc.ensure_application(session, user, job)
    try:
        svc.set_outcome(session, user, job, app, body.outcome, body.reason, body.at)
    except svc.TrackingError as e:
        raise _bad(e)
    session.commit()
    session.refresh(app)
    return svc.application_out(session, app)


@router.post("/jobs/{job_id}/application/snooze")
def snooze(job_id: int, body: SnoozeIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    job, app = _app(session, user, job_id)
    svc.update_fields(session, user, job, app, {"snoozed_until": body.until})
    session.commit()
    return svc.application_out(session, app)


# ----------------------------------------------------------------------- entrevistas y contactos

@router.post("/jobs/{job_id}/application/interviews", status_code=201)
def add_interview(job_id: int, body: InterviewIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    job = _own(session, user, job_id)
    app = svc.ensure_application(session, user, job)
    try:
        it = svc.add_interview(session, user, job, app, body.model_dump())
    except svc.TrackingError as e:
        raise _bad(e)
    session.commit()
    return it


@router.patch("/jobs/{job_id}/application/interviews/{interview_id}")
def patch_interview(job_id: int, interview_id: str, body: InterviewPatch, user: User = Depends(current_user), session: Session = Depends(get_session)):
    _, app = _app(session, user, job_id)
    try:
        it = svc.update_interview(session, app, interview_id, body.model_dump(exclude_unset=True))
    except KeyError:
        raise _gone("Entrevista")
    except svc.TrackingError as e:
        raise _bad(e)
    session.commit()
    return it


@router.delete("/jobs/{job_id}/application/interviews/{interview_id}", status_code=204)
def delete_interview(job_id: int, interview_id: str, user: User = Depends(current_user), session: Session = Depends(get_session)):
    _, app = _app(session, user, job_id)
    try:
        svc.delete_interview(session, app, interview_id)
    except KeyError:
        raise _gone("Entrevista")
    session.commit()


@router.post("/jobs/{job_id}/application/contacts", status_code=201)
def add_contact(job_id: int, body: ContactIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    job = _own(session, user, job_id)
    app = svc.ensure_application(session, user, job)
    c = svc.add_contact(session, app, body.model_dump())
    session.commit()
    return c


@router.patch("/jobs/{job_id}/application/contacts/{contact_id}")
def patch_contact(job_id: int, contact_id: str, body: ContactPatch, user: User = Depends(current_user), session: Session = Depends(get_session)):
    _, app = _app(session, user, job_id)
    try:
        c = svc.update_contact(session, app, contact_id, body.model_dump(exclude_unset=True))
    except KeyError:
        raise _gone("Contacto")
    session.commit()
    return c


@router.delete("/jobs/{job_id}/application/contacts/{contact_id}", status_code=204)
def delete_contact(job_id: int, contact_id: str, user: User = Depends(current_user), session: Session = Depends(get_session)):
    _, app = _app(session, user, job_id)
    try:
        svc.delete_contact(session, app, contact_id)
    except KeyError:
        raise _gone("Contacto")
    session.commit()


# ----------------------------------------------------------------------- archivo inmutable

def _arc_out(a: ApplicationArchive, full: bool) -> dict:
    d = {"id": a.id, "job_id": a.job_id, "application_id": a.application_id, "created_at": svc.aware(a.created_at).isoformat(), "template": a.template,
         "match": a.match, "content_hash": a.content_hash, "title": a.job_snapshot.get("title"), "company": a.job_snapshot.get("company"),
         "has_cv": a.cv_snapshot is not None, "has_cover_letter": bool(a.cover_letter)}
    if full:
        d |= {"job": a.job_snapshot, "cv": a.cv_snapshot, "cv_text": a.cv_text, "cover_letter": a.cover_letter}
    return d


@router.get("/jobs/{job_id}/application/archive")
def job_archive(job_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Versiones archivadas de lo enviado a esta oferta (la oferta original, el CV y la carta exactos). Solo lectura."""
    _own(session, user, job_id)
    return [_arc_out(a, False) for a in svc.archives_of(session, user, job_id)]


@router.get("/archive")
def list_archive(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), user: User = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.exec(select(ApplicationArchive).where(ApplicationArchive.user_id == user.id).order_by(col(ApplicationArchive.id).desc()).offset(offset).limit(limit)).all()
    return [_arc_out(a, False) for a in rows]


@router.get("/archive/{archive_id}")
def get_archive(archive_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    a = session.get(ApplicationArchive, archive_id)
    if not a or a.user_id != user.id:
        raise _gone("Archivo")
    return _arc_out(a, True)


# ----------------------------------------------------------------------- lista, recordatorios, embudo, CSV

@router.get("/applications")
def list_applications(stage: str | None = None, outcome: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                      user: User = Depends(current_user), session: Session = Depends(get_session)):
    stmt = select(ApplicationRow).where(ApplicationRow.user_id == user.id)
    if stage:
        stmt = stmt.where(ApplicationRow.stage == stage)
    if outcome:
        stmt = stmt.where(ApplicationRow.outcome == outcome)
    rows = session.exec(stmt.order_by(col(ApplicationRow.updated_at).desc()).offset(offset).limit(limit)).all()
    return [svc.application_out(session, a) for a in rows]


@router.get("/reminders")
def get_reminders(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Qué requiere tu atención hoy (calculado de fechas y estados: seguimiento a los 7 días sin respuesta, entrevistas próximas, plazos...)."""
    return svc.reminders(session, user)


@router.get("/pipeline/summary")
def pipeline_summary(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Embudo (candidaturas -> respuesta -> entrevista -> oferta), tasa de respuesta, tiempo medio a la primera respuesta y descriptivos."""
    return svc.summary(session, user)


@router.get("/pipeline/export.csv")
def pipeline_csv(user: User = Depends(current_user), session: Session = Depends(get_session)):
    return Response(svc.export_csv(session, user), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="jobhunter-pipeline.csv"'})


@router.get("/pipeline/insights")
def pipeline_insights(narrate: bool = Query(False), user: User = Depends(current_user), session: Session = Depends(get_session), llm=Depends(get_llm)):
    """Análisis agéntico de tus candidaturas: causas probables de rechazos o silencios y acciones para mejorar. Con `narrate` y la IA disponible, redacta una nota breve a partir de esos hechos."""
    from app.ai.report import usable
    from app.tracking import insights
    report = insights.build(session, user)
    model = usable(llm) if narrate else None
    if model and (report["sample"]["sent"] or 0) > 0:
        try:
            system = ("Eres un coach de búsqueda de empleo. Redacta en español, en menos de 110 palabras, un diagnóstico claro y empático SOLO con los hechos dados. "
                      "No inventes datos, motivos ni cifras. Aclara que son hipótesis. Termina con la primera acción concreta.")
            report["narrative"] = model.complete(system, insights.facts_for_llm(report), temperature=0.3).strip()[:1200] or None
            report["mode"] = "llm" if report["narrative"] else "rules"
        except Exception:  # noqa: BLE001 — la IA es un extra: el análisis por reglas ya está completo
            report["mode"] = "rules"
    return report
