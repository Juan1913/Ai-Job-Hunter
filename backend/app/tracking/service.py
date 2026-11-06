"""Lógica del seguimiento de candidaturas. Sin HTTP: la usan app/tracking/api.py y el cambio de estado de app/api/jobs.py.

Principios:
- El sistema NUNCA envía solicitudes: 'aplicada' es lo que el USUARIO declara. Al declararlo se archiva (inmutable) la oferta tal cual
  era y el CV/carta exactos, porque la oferta puede desaparecer.
- Los recordatorios y el embudo se CALCULAN al vuelo a partir de fechas y estados (nada que sincronizar ni caducar).
- Las estadísticas son descriptivas: con pocas candidaturas no prueban causalidad y se rotulan así."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import statistics
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, col, select

from app.db import ApplicationArchive, ApplicationRow, JobEvent, JobRow, TailoredRow, User, utcnow

STAGES = ("saved", "applied", "screening", "interviewing", "offer", "closed")
OUTCOMES = ("offer", "rejected", "no_response", "withdrawn")
INTERVIEW_KINDS = ("screening", "technical", "behavioral", "hr", "final", "other")

FOLLOW_UP_DAYS = 7              # sin respuesta a los 7 días -> sugerir seguimiento
CLOSE_SUGGEST_DAYS = 21         # a las 3 semanas -> sugerir cerrarla como 'sin respuesta'
POST_INTERVIEW_DAYS = 5
DEADLINE_WARN_DAYS = 3
INTERVIEW_SOON_HOURS = 48
NEXT_STEP_WARN_HOURS = 24
SMALL_SAMPLE = 5

CAVEAT = ("Estadísticas descriptivas de TUS candidaturas: con pocas muestras no demuestran que una palabra clave, plantilla o "
          "puntaje cause (o evite) una entrevista. Úsalas para observar, no para concluir.")


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    d = aware(dt)
    return d.isoformat() if d else None


class TrackingError(ValueError):
    """Entrada inválida (el API la traduce a 422)."""


# --------------------------------------------------------------------------- acceso

def get_application(session: Session, user: User, job_id: int) -> ApplicationRow | None:
    return session.exec(select(ApplicationRow).where(ApplicationRow.user_id == user.id, ApplicationRow.job_id == job_id)).first()


def ensure_application(session: Session, user: User, job: JobRow) -> ApplicationRow:
    app = get_application(session, user, job.id)
    if app is None:
        app = ApplicationRow(user_id=user.id, job_id=job.id, stage="saved")
        session.add(app)
        session.flush()
    return app


def _touch(app: ApplicationRow) -> None:
    app.updated_at = utcnow()


def _event(session: Session, user: User, job_id: int, kind: str, note: str | None = None) -> None:
    session.add(JobEvent(user_id=user.id, job_id=job_id, kind=kind, note=note))


def _sync_job_status(session: Session, user: User, job: JobRow, status: str) -> None:
    if job.status != status:
        job.status = status
        session.add(job)
        _event(session, user, job.id, f"status:{status}")


# --------------------------------------------------------------------------- archivo inmutable

def _hash(job: dict, cv: dict | None, cover: str | None) -> str:
    raw = json.dumps({"job": job, "cv": cv, "cover": cover}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def archive_sent(session: Session, user: User, job: JobRow, app: ApplicationRow, emit: bool = True) -> ApplicationArchive | None:
    """Foto de lo enviado. Se añade una fila nueva solo si cambió algo (otra oferta, otro CV u otra carta)."""
    t = session.get(TailoredRow, app.tailored_id) if app.tailored_id else None
    cv = dict(t.data) if t else None
    h = _hash(job.data, cv, app.cover_letter)
    last = session.exec(select(ApplicationArchive).where(ApplicationArchive.application_id == app.id).order_by(col(ApplicationArchive.id).desc())).first()
    if last and last.content_hash == h:
        return last
    arc = ApplicationArchive(user_id=user.id, job_id=job.id, application_id=app.id, job_snapshot=dict(job.data), cv_snapshot=cv,
                             cv_text=(cv or {}).get("text", ""), cover_letter=app.cover_letter, template=app.template, match=job.match, content_hash=h)
    session.add(arc)
    if emit:
        _event(session, user, job.id, "archived", f"hash={h[:10]}")
    return arc


def archives_of(session: Session, user: User, job_id: int) -> list[ApplicationArchive]:
    return list(session.exec(select(ApplicationArchive).where(ApplicationArchive.user_id == user.id, ApplicationArchive.job_id == job_id)
                             .order_by(col(ApplicationArchive.id))).all())


# --------------------------------------------------------------------------- operaciones

def _check_tailored(session: Session, user: User, job: JobRow, tailored_id: int | None) -> int | None:
    if tailored_id is None:
        return None
    t = session.get(TailoredRow, tailored_id)
    if not t or t.user_id != user.id or t.job_id != job.id:
        raise TrackingError("el CV indicado no existe para esta oferta")
    return t.id


def mark_applied(session: Session, user: User, job: JobRow, *, applied_at: datetime | None = None, tailored_id: int | None = None,
                 cover_letter: str | None = None, template: str | None = None, next_step: str | None = None,
                 next_step_at: datetime | None = None, note: str | None = None, emit: bool = True) -> tuple[ApplicationRow, ApplicationArchive | None]:
    """El usuario declara que aplicó (por su cuenta). Vincula el CV exacto (el último de la oferta si no se indica), archiva y sincroniza el estado."""
    app = ensure_application(session, user, job)
    tid = _check_tailored(session, user, job, tailored_id)
    if tid is None and app.tailored_id is None:
        last = session.exec(select(TailoredRow).where(TailoredRow.user_id == user.id, TailoredRow.job_id == job.id).order_by(col(TailoredRow.id).desc())).first()
        tid = last.id if last else None
    if tid is not None:
        app.tailored_id = tid
    if cover_letter is not None:
        app.cover_letter = cover_letter
    if template:
        app.template = template
    app.applied_at = aware(applied_at) or app.applied_at or utcnow()
    if next_step is not None:
        app.next_step = next_step
    if next_step_at is not None:
        app.next_step_at = aware(next_step_at)
    if note:
        app.notes = (app.notes + "\n" if app.notes else "") + note
    if app.stage in ("saved",):
        app.stage = "applied"
    _touch(app)
    session.add(app)
    session.flush()
    _sync_job_status(session, user, job, "applied")
    if emit:                       # el cambio de estado desde PATCH /jobs ya deja su propio evento 'status:applied'
        _event(session, user, job.id, "application:applied", job.data.get("apply_url") or job.data.get("url"))
    arc = archive_sent(session, user, job, app, emit)
    return app, arc


def update_fields(session: Session, user: User, job: JobRow, app: ApplicationRow, fields: dict) -> ApplicationRow:
    """PATCH de fechas/notas/CV usados. Si cambia el CV o la carta de una candidatura ya enviada, se archiva la nueva versión (sin tocar la anterior)."""
    if "tailored_id" in fields:
        app.tailored_id = _check_tailored(session, user, job, fields.pop("tailored_id"))
        sent_changed = True
    else:
        sent_changed = False
    for k in ("next_step", "notes", "template", "expected_salary_currency"):
        if k in fields:
            setattr(app, k, fields[k])
    if "cover_letter" in fields:
        app.cover_letter = fields["cover_letter"]
        sent_changed = True
    for k in ("applied_at", "next_step_at", "deadline_at", "first_response_at", "last_contact_at", "snoozed_until"):
        if k in fields:
            setattr(app, k, aware(fields[k]))
    for k in ("expected_salary_min", "expected_salary_max"):
        if k in fields:
            setattr(app, k, fields[k])
    if app.expected_salary_min and app.expected_salary_max and app.expected_salary_min > app.expected_salary_max:
        raise TrackingError("el mínimo salarial no puede superar al máximo")
    if "stage" in fields and fields["stage"] is not None:
        if fields["stage"] not in STAGES:
            raise TrackingError(f"etapa inválida: usa {', '.join(STAGES)}")
        app.stage = fields["stage"]
        if app.stage in ("applied", "screening", "interviewing", "offer") and app.applied_at is None:
            app.applied_at = utcnow()
        _sync_job_status(session, user, job, {"applied": "applied", "screening": "interview", "interviewing": "interview", "offer": "offer"}.get(app.stage, job.status))
    _touch(app)
    session.add(app)
    session.flush()
    if sent_changed and app.applied_at:
        archive_sent(session, user, job, app)
    return app


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _find(items: list, item_id: str) -> int:
    for i, it in enumerate(items):
        if it.get("id") == item_id:
            return i
    raise KeyError(item_id)


def _dt_str(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return aware(v).isoformat()
    return str(v)


def add_interview(session: Session, user: User, job: JobRow, app: ApplicationRow, data: dict) -> dict:
    if data.get("kind") and data["kind"] not in INTERVIEW_KINDS:
        raise TrackingError(f"tipo de entrevista inválido: usa {', '.join(INTERVIEW_KINDS)}")
    it = {"id": _new_id(), "kind": data.get("kind") or "other", "scheduled_at": _dt_str(data.get("scheduled_at")), "done": bool(data.get("done", False)),
          "interviewer": data.get("interviewer"), "notes": data.get("notes"), "result": data.get("result")}
    app.interviews = [*(app.interviews or []), it]
    if app.first_response_at is None:                       # que te citen a entrevista es la primera respuesta
        app.first_response_at = utcnow()
    if app.applied_at is None:
        app.applied_at = utcnow()
    if app.stage not in ("offer", "closed"):
        app.stage = "screening" if it["kind"] == "screening" and not any(i["kind"] != "screening" for i in app.interviews) else "interviewing"
    _touch(app)
    session.add(app)
    _sync_job_status(session, user, job, "interview" if app.stage in ("screening", "interviewing") else job.status)
    _event(session, user, job.id, "application:interview", it["kind"])
    return it


def update_interview(session: Session, app: ApplicationRow, interview_id: str, fields: dict) -> dict:
    items = [dict(x) for x in (app.interviews or [])]       # copia profunda: el JSON no detecta mutaciones in situ
    i = _find(items, interview_id)
    if fields.get("kind") and fields["kind"] not in INTERVIEW_KINDS:
        raise TrackingError(f"tipo de entrevista inválido: usa {', '.join(INTERVIEW_KINDS)}")
    for k in ("kind", "interviewer", "notes", "result", "done"):
        if k in fields:
            items[i][k] = fields[k]
    if "scheduled_at" in fields:
        items[i]["scheduled_at"] = _dt_str(fields["scheduled_at"])
    app.interviews = items
    _touch(app)
    session.add(app)
    return items[i]


def delete_interview(session: Session, app: ApplicationRow, interview_id: str) -> None:
    items = list(app.interviews or [])
    del items[_find(items, interview_id)]
    app.interviews = items
    _touch(app)
    session.add(app)


def add_contact(session: Session, app: ApplicationRow, data: dict) -> dict:
    c = {"id": _new_id(), **{k: data.get(k) for k in ("name", "role", "email", "phone", "notes")}}
    app.contacts = [*(app.contacts or []), c]
    _touch(app)
    session.add(app)
    return c


def update_contact(session: Session, app: ApplicationRow, contact_id: str, fields: dict) -> dict:
    items = [dict(x) for x in (app.contacts or [])]
    i = _find(items, contact_id)
    for k in ("name", "role", "email", "phone", "notes"):
        if k in fields:
            items[i][k] = fields[k]
    app.contacts = items
    _touch(app)
    session.add(app)
    return items[i]


def delete_contact(session: Session, app: ApplicationRow, contact_id: str) -> None:
    items = list(app.contacts or [])
    del items[_find(items, contact_id)]
    app.contacts = items
    _touch(app)
    session.add(app)


def set_outcome(session: Session, user: User, job: JobRow, app: ApplicationRow, outcome: str | None, reason: str | None = None, at: datetime | None = None) -> ApplicationRow:
    """Resultado final. None lo reabre. 'rejected' u 'offer' cuentan como respuesta; 'no_response' y 'withdrawn' no."""
    if outcome is not None and outcome not in OUTCOMES:
        raise TrackingError(f"resultado inválido: usa {', '.join(OUTCOMES)}")
    if outcome is None:
        app.outcome = app.outcome_reason = app.outcome_at = None
        app.stage = "interviewing" if app.interviews else ("applied" if app.applied_at else "saved")
    else:
        app.outcome, app.outcome_reason, app.outcome_at = outcome, (reason or None), aware(at) or utcnow()
        if outcome in ("offer", "rejected") and app.first_response_at is None:
            app.first_response_at = app.outcome_at
        app.stage = "offer" if outcome == "offer" else "closed"
        _sync_job_status(session, user, job, {"offer": "offer", "rejected": "rejected"}.get(outcome, job.status))
    _touch(app)
    session.add(app)
    _event(session, user, job.id, f"application:outcome:{outcome}", reason)
    return app


def on_job_status(session: Session, user: User, job: JobRow, status: str) -> None:
    """Gancho de PATCH /jobs/{id}: marcar 'applied' crea el seguimiento y archiva; los demás estados mantienen la etapa coherente."""
    if status == "applied":
        app = get_application(session, user, job.id)
        if app is None or app.applied_at is None:
            mark_applied(session, user, job, emit=False)
        return
    if status in ("saved", "interview", "offer", "rejected"):
        app = ensure_application(session, user, job)
        if status == "interview" and app.stage in ("saved", "applied"):
            app.stage = "interviewing"
            app.applied_at = app.applied_at or utcnow()
        elif status == "offer" and app.outcome is None:
            app.stage = "offer"
            app.applied_at = app.applied_at or utcnow()
        elif status == "rejected" and app.outcome is None:
            app.outcome, app.outcome_at, app.stage = "rejected", utcnow(), "closed"
            app.first_response_at = app.first_response_at or app.outcome_at
        _touch(app)
        session.add(app)


# --------------------------------------------------------------------------- recordatorios

def _days(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 86400


def reminders_for(app: ApplicationRow, job: dict, now: datetime) -> list[dict]:
    """Recordatorios calculados de una candidatura. `severity`: high (actúa hoy) | medium | low."""
    if app.snoozed_until and aware(app.snoozed_until) > now:
        return []
    out: list[dict] = []
    base = {"job_id": app.job_id, "title": job.get("title", ""), "company": job.get("company", "")}

    def add(kind: str, severity: str, due: datetime | None, msg: str) -> None:
        out.append({**base, "kind": kind, "severity": severity, "due_at": _iso(due), "message": msg})

    closed = app.stage == "closed" or app.outcome in ("rejected", "withdrawn", "no_response")
    applied = aware(app.applied_at)
    if not closed and applied and app.first_response_at is None and not app.interviews:
        waited = _days(now, applied)
        last = aware(app.last_contact_at)
        since_last = _days(now, last) if last else waited
        if waited >= CLOSE_SUGGEST_DAYS:
            add("close_suggestion", "medium", applied + timedelta(days=CLOSE_SUGGEST_DAYS),
                f"Llevas {int(waited)} días sin respuesta. Considera cerrarla como 'sin respuesta' y seguir con otras.")
        elif waited >= FOLLOW_UP_DAYS and since_last >= FOLLOW_UP_DAYS:
            add("follow_up", "high", applied + timedelta(days=FOLLOW_UP_DAYS),
                f"Sin respuesta tras {int(waited)} días: envía un mensaje breve de seguimiento al reclutador (tú decides; el sistema no envía nada).")
    ns = aware(app.next_step_at)
    if not closed and ns:
        hours = (ns - now).total_seconds() / 3600
        label = app.next_step or "siguiente paso"
        if hours < 0:
            add("next_step_overdue", "high", ns, f"Venció el siguiente paso: {label}.")
        elif hours <= NEXT_STEP_WARN_HOURS:
            add("next_step_due", "high", ns, f"Hoy o mañana: {label}.")
    dl = aware(app.deadline_at)
    if dl and applied is None and app.stage in ("saved",) and app.outcome is None:
        d = _days(dl, now)
        if d < 0:
            add("deadline_passed", "low", dl, "La oferta ya venció; probablemente no acepte más postulaciones.")
        elif d <= DEADLINE_WARN_DAYS:
            add("deadline_soon", "high", dl, f"La oferta vence en {max(int(d), 0)} día(s) y aún no has aplicado.")
    for it in app.interviews or []:
        when = aware(datetime.fromisoformat(it["scheduled_at"])) if it.get("scheduled_at") else None
        if not when or it.get("done"):
            continue
        h = (when - now).total_seconds() / 3600
        if 0 <= h <= INTERVIEW_SOON_HOURS:
            add("interview_soon", "high", when, f"Entrevista ({it.get('kind')}) en {int(h)} h: repasa tus historias y preguntas de preparación.")
        elif h < 0 and not closed:
            add("interview_pending_notes", "medium", when, "La entrevista ya pasó: marca el resultado y anota lo que preguntaron.")
    done = [aware(datetime.fromisoformat(i["scheduled_at"])) for i in app.interviews or [] if i.get("done") and i.get("scheduled_at")]
    if not closed and app.outcome is None and done:
        waited = _days(now, max(done))
        if waited >= POST_INTERVIEW_DAYS and not any(r["kind"] in ("next_step_due", "next_step_overdue") for r in out):
            add("post_interview_follow_up", "medium", max(done) + timedelta(days=POST_INTERVIEW_DAYS),
                f"Han pasado {int(waited)} días desde tu última entrevista sin resultado: pregunta por los tiempos del proceso.")
    return out


_SEV = {"high": 0, "medium": 1, "low": 2}


def reminders(session: Session, user: User, now: datetime | None = None) -> list[dict]:
    now = aware(now) or utcnow()
    apps = session.exec(select(ApplicationRow).where(ApplicationRow.user_id == user.id)).all()
    jobs = {j.id: j.data for j in session.exec(select(JobRow).where(JobRow.user_id == user.id)).all()}
    out = [r for a in apps for r in reminders_for(a, jobs.get(a.job_id, {}), now)]
    out.sort(key=lambda r: (_SEV[r["severity"]], r["due_at"] or ""))
    return out


# --------------------------------------------------------------------------- embudo y estadísticas

def _interviewed(a: ApplicationRow) -> bool:
    return bool(a.interviews) or a.stage in ("interviewing", "offer") or a.outcome == "offer"


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def summary(session: Session, user: User, now: datetime | None = None) -> dict:
    now = aware(now) or utcnow()
    apps = list(session.exec(select(ApplicationRow).where(ApplicationRow.user_id == user.id)).all())
    jobs = {j.id: j for j in session.exec(select(JobRow).where(JobRow.user_id == user.id)).all()}
    cvs = {t.id: t for t in session.exec(select(TailoredRow).where(TailoredRow.user_id == user.id)).all()}
    sent = [a for a in apps if a.applied_at is not None]
    responded = [a for a in sent if a.first_response_at is not None]
    interviewed = [a for a in sent if _interviewed(a)]
    offers = [a for a in sent if a.outcome == "offer" or a.stage == "offer"]
    waits = [max(_days(aware(a.first_response_at), aware(a.applied_at)), 0.0) for a in responded]
    by_stage = {s: 0 for s in STAGES}
    by_outcome = {o: 0 for o in OUTCOMES}
    for a in apps:
        by_stage[a.stage] = by_stage.get(a.stage, 0) + 1
        if a.outcome:
            by_outcome[a.outcome] += 1
    stale = [a for a in sent if a.first_response_at is None and not a.interviews and a.outcome is None and _days(now, aware(a.applied_at)) >= FOLLOW_UP_DAYS]

    def group(keyfn) -> list[dict]:
        buckets: dict[str, list[ApplicationRow]] = {}
        for a in sent:
            for k in keyfn(a):
                buckets.setdefault(k, []).append(a)
        rows = [{"key": k, "applications": len(v), "interviews": sum(1 for a in v if _interviewed(a)), "responses": sum(1 for a in v if a.first_response_at),
                 "interview_rate": _rate(sum(1 for a in v if _interviewed(a)), len(v)), "small_sample": len(v) < SMALL_SAMPLE} for k, v in buckets.items()]
        return sorted(rows, key=lambda r: (-r["applications"], r["key"]))

    def cv_of(a: ApplicationRow) -> dict:
        t = cvs.get(a.tailored_id) if a.tailored_id else None
        return t.data if t else {}

    def score_band(a: ApplicationRow) -> list[str]:
        s = (cv_of(a).get("ats") or {}).get("score")
        return [] if s is None else ["ATS < 70" if s < 70 else "ATS 70-84" if s < 85 else "ATS >= 85"]

    kw = [r for r in group(lambda a: sorted({str(s).strip().lower() for s in cv_of(a).get("skills", []) if str(s).strip()})) if r["applications"] >= 2][:15]
    return {
        "as_of": now.isoformat(), "total_tracked": len(apps), "applied": len(sent), "by_stage": by_stage, "by_outcome": by_outcome,
        "funnel": [{"step": "applied", "count": len(sent)}, {"step": "responded", "count": len(responded)},
                   {"step": "interviewed", "count": len(interviewed)}, {"step": "offer", "count": len(offers)}],
        "response_rate": _rate(len(responded), len(sent)), "interview_rate": _rate(len(interviewed), len(sent)), "offer_rate": _rate(len(offers), len(sent)),
        "avg_days_to_first_response": round(statistics.mean(waits), 1) if waits else None,
        "median_days_to_first_response": round(statistics.median(waits), 1) if waits else None,
        "awaiting_followup": len(stale),
        "descriptive": {"by_keyword": kw, "by_template": group(lambda a: [a.template or "harvard"]), "by_ats_band": group(score_band),
                        "by_source": group(lambda a: [str((jobs[a.job_id].data if a.job_id in jobs else {}).get("source", "?")).split(":")[0]])},
        "caveat": CAVEAT,
    }


# --------------------------------------------------------------------------- CSV

CSV_COLUMNS = ["job_id", "title", "company", "source", "url", "stage", "applied_at", "next_step", "next_step_at", "deadline_at", "first_response_at",
               "days_to_first_response", "interviews", "outcome", "outcome_reason", "tailored_cv_id", "ats_score", "template", "archived_versions", "notes"]


def _safe_cell(v) -> str:
    """Anti 'CSV injection': una celda que empieza con = + - @ se interpretaría como fórmula en Excel."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def export_csv(session: Session, user: User) -> str:
    apps = session.exec(select(ApplicationRow).where(ApplicationRow.user_id == user.id).order_by(col(ApplicationRow.id))).all()
    jobs = {j.id: j.data for j in session.exec(select(JobRow).where(JobRow.user_id == user.id)).all()}
    cvs = {t.id: t for t in session.exec(select(TailoredRow).where(TailoredRow.user_id == user.id)).all()}
    n_arch: dict[int, int] = {}
    for a in session.exec(select(ApplicationArchive).where(ApplicationArchive.user_id == user.id)).all():
        n_arch[a.application_id] = n_arch.get(a.application_id, 0) + 1
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for a in apps:
        j = jobs.get(a.job_id, {})
        t = cvs.get(a.tailored_id) if a.tailored_id else None
        d = round(max(_days(aware(a.first_response_at), aware(a.applied_at)), 0), 1) if a.first_response_at and a.applied_at else ""
        w.writerow([_safe_cell(x) for x in (a.job_id, j.get("title"), j.get("company"), j.get("source"), j.get("url"), a.stage, _iso(a.applied_at), a.next_step,
                                           _iso(a.next_step_at), _iso(a.deadline_at), _iso(a.first_response_at), d, len(a.interviews or []), a.outcome,
                                           a.outcome_reason, a.tailored_id, t.ats_score if t else "", a.template, n_arch.get(a.id, 0), a.notes)])
    return buf.getvalue()


def application_out(session: Session, app: ApplicationRow, now: datetime | None = None) -> dict:
    now = aware(now) or utcnow()
    job = session.get(JobRow, app.job_id)
    jd = job.data if job else {}
    return {
        "id": app.id, "job_id": app.job_id, "title": jd.get("title"), "company": jd.get("company"), "job_status": job.status if job else None,
        "stage": app.stage, "tailored_id": app.tailored_id, "template": app.template, "cover_letter": app.cover_letter,
        "applied_at": _iso(app.applied_at), "next_step": app.next_step, "next_step_at": _iso(app.next_step_at), "deadline_at": _iso(app.deadline_at),
        "first_response_at": _iso(app.first_response_at), "last_contact_at": _iso(app.last_contact_at), "outcome": app.outcome,
        "outcome_reason": app.outcome_reason, "outcome_at": _iso(app.outcome_at), "interviews": app.interviews or [], "contacts": app.contacts or [],
        "notes": app.notes, "expected_salary": {"min": app.expected_salary_min, "max": app.expected_salary_max, "currency": app.expected_salary_currency},
        "snoozed_until": _iso(app.snoozed_until), "reminders": reminders_for(app, jd, now), "created_at": _iso(app.created_at), "updated_at": _iso(app.updated_at),
    }
