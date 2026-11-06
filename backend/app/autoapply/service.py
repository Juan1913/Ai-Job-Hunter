"""Auto-postulación OPCIONAL y bajo control del usuario: política, elegibilidad, CV (general o hecho para el puesto), ejecución y auditoría.

Garantías (todas verificadas en tests):
  · apagada por defecto; sin política `approval`/`auto` no envía nada;
  · empieza en ENSAYO (rellena sin enviar) hasta que el usuario lo desactive a propósito;
  · solo portales con formulario público; los que exigen iniciar sesión (LinkedIn, Computrabajo...) quedan «solo manual»;
  · nunca inventa respuestas ni resuelve captchas: ante lo desconocido se detiene y lo deja para el usuario;
  · tope diario, interruptor de pausa, una oferta nunca se postula dos veces, todo queda en ApplyAttempt y en el seguimiento."""
from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlmodel import Session, col, select

from app.autoapply.form import Applicant, FormResult, run_form
from app.autoapply.models import ApplyData, AutoApplyPolicy
from app.config import settings
from app.db import ApplyAttempt, ApplyDataRow, AutoApplyPolicyRow, JobRow, ProfileRow, TailoredRow, User, engine, utcnow
from app.domain.job import Job
from app.domain.profile import Profile

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autoapply")     # un navegador a la vez: educado con los portales y ligero en CPU
_RUN_LOCK = threading.Lock()

AUTOMATABLE = {"greenhouse", "lever", "ashby", "workable", "teamtailor", "recruitee", "bamboohr", "careers"}
MANUAL_ONLY_WHY = "Este portal exige iniciar sesión o tiene verificaciones propias: abre la oferta y postula tú."
ACTIVE = ("awaiting_approval", "queued", "running", "submitted", "dry_run", "unconfirmed")      # no se vuelve a intentar mientras exista uno de estos
COUNTED = ("queued", "running", "submitted", "dry_run", "unconfirmed", "needs_manual", "failed")
REASONS = {
    "policy_off": "La auto-postulación está apagada.", "paused": "Está en pausa.", "portal_off": "Este portal está desactivado en tu política.",
    "manual_only": MANUAL_ONLY_WHY, "vetoed": "La evaluación la descarta (veto).", "low_score": "El encaje está por debajo de tu mínimo.",
    "not_recommended": "No está marcada como «vale la pena postular».", "closed": "La oferta está cerrada.", "already": "Ya la postulaste o hay un intento en curso.",
    "cap": "Llegaste al tope diario.", "missing_data": "Faltan datos tuyos para el formulario.", "no_profile": "Sube un CV primero.",
}


def source_key(job: dict) -> str:
    return str(job.get("source", "")).split(":")[0]


def adapter_for(job: dict) -> str | None:
    k = source_key(job)
    return k if k in AUTOMATABLE else None


def apply_url(job: dict) -> str:
    url = job.get("apply_url") or job.get("url") or ""
    p = urlsplit(url)
    if p.hostname in ("jobs.lever.co", "jobs.eu.lever.co") and not p.path.rstrip("/").endswith("/apply"):
        return url.rstrip("/").split("?")[0] + "/apply"
    return url


# ----------------------------------------------------------------------------- política y datos
def get_policy(s: Session, user_id: int) -> AutoApplyPolicy:
    row = s.get(AutoApplyPolicyRow, user_id)
    return AutoApplyPolicy.model_validate(row.data) if row else AutoApplyPolicy()


def put_policy(s: Session, user_id: int, p: AutoApplyPolicy) -> AutoApplyPolicy:
    row = s.get(AutoApplyPolicyRow, user_id) or AutoApplyPolicyRow(user_id=user_id)
    row.data, row.updated_at = p.model_dump(), utcnow()
    s.add(row)
    s.commit()
    return p


def get_data(s: Session, user_id: int) -> ApplyData:
    row = s.get(ApplyDataRow, user_id)
    return ApplyData.model_validate(row.data) if row else ApplyData()


def put_data(s: Session, user_id: int, d: ApplyData) -> ApplyData:
    row = s.get(ApplyDataRow, user_id) or ApplyDataRow(user_id=user_id)
    row.data, row.updated_at = d.model_dump(), utcnow()
    s.add(row)
    s.commit()
    return d


def applicant_for(profile: Profile, data: ApplyData, cover: str = "") -> Applicant:
    return Applicant(full_name=data.full_name or profile.full_name, email=data.email or profile.email or "", phone=data.phone or profile.phone or "",
                     city=data.city or (profile.location or ""), data=data, cover_letter=cover)


def missing_data(profile: Profile, data: ApplyData) -> list[str]:
    a = applicant_for(profile, data)
    return [n for n, v in (("nombre", a.full_name), ("correo", a.email), ("teléfono", a.phone)) if not v.strip()]


def effective_mode(policy: AutoApplyPolicy, job: dict) -> str:
    return policy.portal_modes.get(source_key(job), policy.mode)


def used_today(s: Session, user_id: int, now: datetime) -> int:
    since = now - timedelta(hours=24)
    return len(s.exec(select(ApplyAttempt.id).where(ApplyAttempt.user_id == user_id, col(ApplyAttempt.status).in_(COUNTED), col(ApplyAttempt.created_at) >= since)).all())


def blockers(s: Session, row: JobRow, policy: AutoApplyPolicy, profile: Profile | None, data: ApplyData, now: datetime, *, manual: bool = False) -> list[str]:
    """Códigos por los que NO se puede postular. `manual`: lo pidió el usuario a mano para esta oferta (no aplican modo, puntaje ni recomendación)."""
    out: list[str] = []
    job = row.data
    if profile is None:
        return ["no_profile"]
    if not manual:
        if policy.paused:
            out.append("paused")
        mode = effective_mode(policy, job)
        if mode == "off":
            out.append("portal_off" if policy.mode != "off" else "policy_off")
        ev = row.evaluation or {}
        if ev.get("vetoes"):
            out.append("vetoed")
        if (ev.get("score") or 0) < policy.min_score:
            out.append("low_score")
        if policy.only_recommended and ev.get("recommendation") != "apply":
            out.append("not_recommended")
    if adapter_for(job) is None:
        out.append("manual_only")
    if job.get("closed"):
        out.append("closed")
    if s.exec(select(ApplyAttempt.id).where(ApplyAttempt.job_id == row.id, col(ApplyAttempt.status).in_(ACTIVE))).first() or row.status in ("applied", "interview", "offer"):
        out.append("already")
    if used_today(s, row.user_id, now) >= policy.daily_cap:
        out.append("cap")
    if missing_data(profile, data):
        out.append("missing_data")
    return out


# ----------------------------------------------------------------------------- CV
def base_cv(profile: Profile):
    """CV general: todo el perfil real, sin adaptarlo a ninguna oferta (nada inventado)."""
    from app.ai.writer import tailor_extractive
    from app.cv.finalize import finalize_cv
    job = Job(title=profile.headline or "Perfil", company="", url="base://perfil", source="base", description="")
    cv = tailor_extractive(profile, job, [], "base_cv")
    return finalize_cv(cv, job, [], None, profile=profile)


def prepare_cv(s: Session, user: User, row: JobRow, profile: Profile, cv_mode: str) -> TailoredRow:
    if cv_mode == "tailored":
        existing = s.exec(select(TailoredRow).where(TailoredRow.user_id == user.id, TailoredRow.job_id == row.id).order_by(col(TailoredRow.id).desc())).first()
        if existing:
            return existing
        from app.api import jobs as api
        from app.llm.factory import build_llm
        out = api.tailor(row.id, api.TailorIn(confirmed=[], save_confirmed=False), user, s, build_llm("fast", user.id), build_llm("writer", user.id))
        return s.get(TailoredRow, out.id)
    cv = base_cv(profile)
    t = TailoredRow(user_id=user.id, job_id=row.id, data=cv.model_dump(mode="json"), confirmed=[], ats_score=cv.ats.score if cv.ats else 0)
    s.add(t)
    s.commit()
    s.refresh(t)
    return t


def _cover(s: Session, user: User, row: JobRow) -> str:
    try:
        from app.api import jobs as api
        from app.llm.factory import build_llm
        out = api.cover_letter(row.id, api.CoverLetterIn(confirmed=[], lang=None), user, s, build_llm("fast", user.id), build_llm("writer", user.id))
        return str(out.get("text") or "")
    except Exception:  # noqa: BLE001 — la carta es opcional: sin ella se postula igual
        log.warning("auto-postulación: no se pudo generar la carta de la oferta %s", row.id)
        return ""


# ----------------------------------------------------------------------------- intentos
def _touch(a: ApplyAttempt, **f) -> None:
    for k, v in f.items():
        setattr(a, k, v)
    a.updated_at = utcnow()


def create_attempt(s: Session, user_id: int, row: JobRow, *, status: str, trigger: str, cv_mode: str, dry_run: bool) -> ApplyAttempt:
    a = ApplyAttempt(user_id=user_id, job_id=row.id, status=status, trigger=trigger, adapter=adapter_for(row.data) or "", cv_mode=cv_mode, dry_run=dry_run)
    s.add(a)
    s.commit()
    s.refresh(a)
    return a


def start(attempt_id: int) -> None:
    if settings.run_inline:
        execute(attempt_id)
    else:
        _pool.submit(execute, attempt_id)


def execute(attempt_id: int) -> None:
    """Prepara el CV, rellena (y si no es ensayo, envía) el formulario y deja constancia. Nunca lanza: todo error queda en el intento."""
    with Session(engine) as s:
        a = s.get(ApplyAttempt, attempt_id)
        if a is None or a.status != "queued":
            return
        _touch(a, status="running")
        s.add(a)
        s.commit()
        path = None
        try:
            user, row = s.get(User, a.user_id), s.get(JobRow, a.job_id)
            prow = s.get(ProfileRow, a.user_id)
            if user is None or row is None or prow is None:
                raise RuntimeError("falta el usuario, la oferta o el perfil")
            profile, policy, data = Profile.model_validate(prow.data), get_policy(s, a.user_id), get_data(s, a.user_id)
            if used_today(s, a.user_id, utcnow()) > policy.daily_cap:
                _touch(a, status="needs_manual", error="Tope diario alcanzado: reintenta mañana o súbelo en tu política.", finished_at=utcnow())
                s.add(a)
                s.commit()
                return
            from app.scout.netguard import assert_public_url
            url = assert_public_url(apply_url(row.data))
            t = prepare_cv(s, user, row, profile, a.cv_mode)
            cover = _cover(s, user, row) if policy.cover_letter else ""
            from app.cv.pdf import render_pdf
            from app.domain.tailoring import TailoredCV
            fd, path = tempfile.mkstemp(suffix=".pdf", prefix=re.sub(r"\W+", "_", profile.full_name or "CV") + "_")
            with os.fdopen(fd, "wb") as fh:
                fh.write(render_pdf(TailoredCV.model_validate(t.data)))
            with _RUN_LOCK:
                res: FormResult = run_form(url, applicant_for(profile, data, cover), path, dry_run=a.dry_run, headless=settings.autoapply_headless)
            detail = {"message": res.message, "filled": res.filled, "unanswered": res.unanswered, "steps": res.steps, "final_url": res.final_url, "cv_ats": t.ats_score}
            _touch(a, status=res.status, tailored_id=t.id, detail=detail, error=None if res.status != "failed" else res.message, finished_at=utcnow())
            s.add(a)
            if res.status == "submitted":
                from app.tracking import service as tracking
                tracking.mark_applied(s, user, row, tailored_id=t.id, cover_letter=cover or None, template="harvard", note=f"Auto-postulación ({a.adapter})")
            s.commit()
            _notify(s, a, row)
        except Exception as e:  # noqa: BLE001
            log.exception("auto-postulación %s falló", attempt_id)
            s.rollback()
            a = s.get(ApplyAttempt, attempt_id)
            _touch(a, status="failed", error=f"{type(e).__name__}: {str(e)[:300]}", finished_at=utcnow())
            s.add(a)
            s.commit()
        finally:
            if path and os.path.exists(path):
                os.remove(path)


_TITLE = {"submitted": "Postulé por ti", "dry_run": "Ensayo listo", "needs_manual": "Necesito que termines tú", "unconfirmed": "Revisa si se envió", "failed": "No pude postular"}


def _notify(s: Session, a: ApplyAttempt, row: JobRow) -> None:
    try:
        from app.notifications import service as notif
        notif.create(s, a.user_id, "auto_apply", f"{_TITLE.get(a.status, 'Auto-postulación')}: {row.data.get('title', '')}", (a.detail or {}).get("message") or a.error or "",
                     job_id=row.id, data={"attempt_id": a.id, "status": a.status}, dedup_key=f"auto_apply:{a.id}:{a.status}")
        s.commit()
    except Exception:  # noqa: BLE001 — un aviso fallido no deshace una postulación
        log.warning("no se pudo crear el aviso de la auto-postulación %s", a.id)


def on_new_jobs(s: Session, user: User, rows: list[JobRow], now: datetime | None = None) -> dict:
    """Paso posterior a cada búsqueda: crea intentos para las ofertas nuevas que cumplen la política (approval: esperan tu clic; auto: se ejecutan)."""
    now = now or utcnow()
    res = {"queued": 0, "awaiting": 0, "skipped": 0}
    policy = get_policy(s, user.id)
    if policy.mode == "off" and not any(m != "off" for m in policy.portal_modes.values()):
        return res
    prow = s.get(ProfileRow, user.id)
    if prow is None:
        return res
    profile, data = Profile.model_validate(prow.data), get_data(s, user.id)
    todo = [r for r in rows if not r.evaluation]
    if todo:
        from app.evaluation import service as evaluation
        evaluation.evaluate_many(s, todo, profile)
    for r in sorted(rows, key=lambda r: -((r.evaluation or {}).get("score") or 0)):
        if blockers(s, r, policy, profile, data, now):
            res["skipped"] += 1
            continue
        approval = effective_mode(policy, r.data) == "approval"
        a = create_attempt(s, user.id, r, status="awaiting_approval" if approval else "queued", trigger="auto", cv_mode=policy.cv_mode, dry_run=policy.dry_run)
        if approval:
            res["awaiting"] += 1
            try:
                from app.notifications import service as notif
                notif.create(s, user.id, "auto_apply", f"Lista para postular: {r.data.get('title', '')}", "Aprueba con un clic y la envío con tu CV.", job_id=r.id,
                             data={"attempt_id": a.id, "status": a.status}, dedup_key=f"auto_apply:{a.id}:awaiting")
                s.commit()
            except Exception:  # noqa: BLE001
                pass
        else:
            res["queued"] += 1
            start(a.id)
    return res
