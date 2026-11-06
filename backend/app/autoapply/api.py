"""REST de la auto-postulación (/api/v1/me/auto-apply). Todo por usuario; una oferta o intento ajeno responde 404."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.api.deps import current_user
from app.api.jobs import _own
from app.autoapply import service as svc
from app.autoapply.models import ApplyData, AutoApplyPolicy, CvMode
from app.db import ApplyAttempt, JobRow, ProfileRow, User, get_session, utcnow
from app.domain.profile import Profile

router = APIRouter(prefix="/me/auto-apply", tags=["auto-apply"])


def _profile(s: Session, user: User) -> Profile | None:
    row = s.get(ProfileRow, user.id)
    return Profile.model_validate(row.data) if row and row.data else None


def _out(s: Session, a: ApplyAttempt) -> dict:
    job = s.get(JobRow, a.job_id)
    d = job.data if job else {}
    return {"id": a.id, "job_id": a.job_id, "title": d.get("title"), "company": d.get("company"), "source": d.get("source"), "status": a.status, "trigger": a.trigger,
            "adapter": a.adapter, "cv_mode": a.cv_mode, "tailored_id": a.tailored_id, "dry_run": a.dry_run, "detail": a.detail or {}, "error": a.error,
            "apply_url": svc.apply_url(d) if d else None, "created_at": a.created_at.isoformat(), "finished_at": a.finished_at.isoformat() if a.finished_at else None}


def _attempt(s: Session, user: User, attempt_id: int) -> ApplyAttempt:
    a = s.get(ApplyAttempt, attempt_id)
    if not a or a.user_id != user.id:
        raise HTTPException(404, "Intento no encontrado")
    return a


@router.get("/capabilities")
def capabilities():
    """Qué portales puede automatizar este despliegue y cuáles quedan solo manuales (y por qué)."""
    try:
        import playwright  # noqa: F401
        browser = True
    except ImportError:
        browser = False
    return {"browser_available": browser, "automatable": sorted(svc.AUTOMATABLE), "manual_only_reason": svc.MANUAL_ONLY_WHY, "reasons": svc.REASONS}


@router.get("/policy")
def get_policy(user: User = Depends(current_user), s: Session = Depends(get_session)):
    return svc.get_policy(s, user.id)


@router.put("/policy")
def put_policy(body: AutoApplyPolicy, user: User = Depends(current_user), s: Session = Depends(get_session)):
    return svc.put_policy(s, user.id, body)


@router.get("/data")
def get_data(user: User = Depends(current_user), s: Session = Depends(get_session)):
    return svc.get_data(s, user.id)


@router.put("/data")
def put_data(body: ApplyData, user: User = Depends(current_user), s: Session = Depends(get_session)):
    return svc.put_data(s, user.id, body)


@router.get("/jobs/{job_id}/preview")
def preview(job_id: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    """Qué pasaría si le pides al sistema postular a esta oferta: portal, datos que faltan y motivos que lo impiden."""
    row = _own(s, user, job_id)
    profile, policy, data = _profile(s, user), svc.get_policy(s, user.id), svc.get_data(s, user.id)
    codes = svc.blockers(s, row, policy, profile, data, utcnow(), manual=True)
    return {"adapter": svc.adapter_for(row.data), "can_run": not codes, "blockers": [{"code": c, "message": svc.REASONS[c]} for c in codes],
            "missing_data": svc.missing_data(profile, data) if profile else [], "cv_mode": policy.cv_mode, "dry_run": policy.dry_run, "apply_url": svc.apply_url(row.data),
            "daily_left": max(0, policy.daily_cap - svc.used_today(s, user.id, utcnow()))}


class ApplyNowIn(BaseModel):
    confirm: bool = False                  # el usuario debe confirmar a propósito
    cv_mode: CvMode | None = None
    dry_run: bool | None = None


@router.post("/jobs/{job_id}", status_code=202)
def apply_now(job_id: int, body: ApplyNowIn, user: User = Depends(current_user), s: Session = Depends(get_session)):
    """«Postula por mí» para UNA oferta, pedido a mano. Exige `confirm: true`. Respeta tope diario, portales compatibles y datos completos."""
    if not body.confirm:
        raise HTTPException(422, "Confirma que quieres que postule por ti (confirm: true)")
    row = _own(s, user, job_id)
    profile, policy, data = _profile(s, user), svc.get_policy(s, user.id), svc.get_data(s, user.id)
    codes = svc.blockers(s, row, policy, profile, data, utcnow(), manual=True)
    if codes:
        raise HTTPException(409, " ".join(svc.REASONS[c] for c in codes))
    a = svc.create_attempt(s, user.id, row, status="queued", trigger="manual", cv_mode=body.cv_mode or policy.cv_mode, dry_run=policy.dry_run if body.dry_run is None else body.dry_run)
    svc.start(a.id)
    s.refresh(a)
    return _out(s, a)


@router.get("/attempts")
def attempts(status: str | None = None, limit: int = Query(100, ge=1, le=300), user: User = Depends(current_user), s: Session = Depends(get_session)):
    stmt = select(ApplyAttempt).where(ApplyAttempt.user_id == user.id)
    if status:
        stmt = stmt.where(ApplyAttempt.status == status)
    return [_out(s, a) for a in s.exec(stmt.order_by(col(ApplyAttempt.id).desc()).limit(limit)).all()]


@router.post("/attempts/{attempt_id}/approve", status_code=202)
def approve(attempt_id: int, dry_run: bool | None = None, user: User = Depends(current_user), s: Session = Depends(get_session)):
    a = _attempt(s, user, attempt_id)
    if a.status != "awaiting_approval":
        raise HTTPException(409, "Este intento no está esperando aprobación")
    row = s.get(JobRow, a.job_id)
    codes = [c for c in svc.blockers(s, row, svc.get_policy(s, user.id), _profile(s, user), svc.get_data(s, user.id), utcnow(), manual=True) if c != "already"]
    if codes:
        raise HTTPException(409, " ".join(svc.REASONS[c] for c in codes))
    a.status, a.trigger = "queued", "approved"
    if dry_run is not None:
        a.dry_run = dry_run
    a.updated_at = utcnow()
    s.add(a)
    s.commit()
    svc.start(a.id)
    s.refresh(a)
    return _out(s, a)


@router.post("/attempts/{attempt_id}/reject")
def reject(attempt_id: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    a = _attempt(s, user, attempt_id)
    if a.status != "awaiting_approval":
        raise HTTPException(409, "Este intento no está esperando aprobación")
    a.status, a.finished_at, a.updated_at = "rejected", utcnow(), utcnow()
    s.add(a)
    s.commit()
    return _out(s, a)


@router.post("/attempts/{attempt_id}/retry", status_code=202)
def retry(attempt_id: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    a = _attempt(s, user, attempt_id)
    if a.status not in ("needs_manual", "failed", "unconfirmed", "dry_run"):
        raise HTTPException(409, "Solo se reintentan intentos terminados sin éxito o ensayos")
    row = s.get(JobRow, a.job_id)
    if row.status in ("applied", "interview", "offer"):
        raise HTTPException(409, svc.REASONS["already"])
    a.status, a.error, a.finished_at, a.updated_at = "queued", None, None, utcnow()
    s.add(a)
    s.commit()
    svc.start(a.id)
    s.refresh(a)
    return _out(s, a)
