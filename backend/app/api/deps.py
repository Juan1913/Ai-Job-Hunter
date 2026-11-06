import re
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, col, func, select

from app.config import settings
from app.db import JobEvent, ProfileRow, TailoredRow, User, get_session, utcnow
from app.domain.profile import Profile
from app.llm.factory import build_llm
from app.llm.guard import GuardedLLM
from app.observability import metrics, user_id_var
from app.security import decode_claims, password_version

bearer = HTTPBearer(auto_error=False)


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(bearer), session: Session = Depends(get_session)) -> User:
    claims = decode_claims(cred.credentials) if cred else None
    user = None
    if claims:
        try:
            user = session.get(User, int(claims["sub"]))
        except (KeyError, ValueError):
            user = None
        # El token queda invalidado si la contraseña cambió después de emitirlo.
        if user and claims.get("pv") and claims["pv"] != password_version(user.password_hash):
            user = None
    if not user:
        raise HTTPException(401, "No autenticado", headers={"WWW-Authenticate": "Bearer"})
    user_id_var.set(user.id)
    return user


def _uid(user) -> int | None:
    return user.id if isinstance(user, User) else None     # llamado a mano (sin petición): tarea del sistema


def get_llm(user: User = Depends(current_user)) -> GuardedLLM:
    """Modelo ligero (extracción, clasificación, matching) según JH_AI_MODE y el usuario (consentimiento/presupuesto)."""
    return build_llm("fast", _uid(user))


def get_writer(user: User = Depends(current_user)) -> GuardedLLM:
    """Modelo de redacción de CVs."""
    return build_llm("writer", _uid(user))


def load_profile(session: Session, user: User) -> Profile:
    row = session.get(ProfileRow, user.id)
    if not row or not row.data:
        raise HTTPException(409, "Sube primero un CV para construir tu perfil")
    return Profile.model_validate(row.data)


# ------------------------------------------------------------------ cuotas diarias por usuario
_TAILOR = re.compile(r"/jobs/\d+/tailor$")
_ANALYZE = re.compile(r"/jobs/\d+/analyze$")


def _quota_hit(kind: str, used: int, limit: int) -> None:
    if limit and used >= limit:
        metrics.inc("jh_rate_limited_total", scope=f"quota_{kind}")
        raise HTTPException(429, f"Has alcanzado el límite diario de {kind} ({limit}). Inténtalo mañana",
                            headers={"Retry-After": "3600"})


def check_quota(kind: str, user: User, session: Session) -> None:
    """kind: search | tailor | analyze. Límite en ventana de 24 h por usuario (la CPU del LLM es compartida)."""
    since = utcnow() - timedelta(hours=24)
    if kind == "search" and settings.quota_searches_per_day:
        from app.scheduling.service import manual_runs_since  # las búsquedas programadas no gastan la cuota manual del usuario
        _quota_hit("búsquedas", manual_runs_since(session, user.id, since), settings.quota_searches_per_day)
    elif kind == "tailor" and settings.quota_tailor_per_day:
        n = session.exec(select(func.count()).select_from(TailoredRow).where(TailoredRow.user_id == user.id, col(TailoredRow.created_at) >= since)).one()
        _quota_hit("adaptaciones de CV", n, settings.quota_tailor_per_day)
    elif kind == "analyze" and settings.quota_analyze_per_day:
        n = session.exec(select(func.count()).select_from(JobEvent).where(JobEvent.user_id == user.id, JobEvent.kind == "analyzed",
                                                                          col(JobEvent.ts) >= since)).one()
        _quota_hit("análisis", n, settings.quota_analyze_per_day)


def enforce_quotas(request: Request, user: User = Depends(current_user), session: Session = Depends(get_session)) -> None:
    """Dependencia de router: aplica la cuota según la ruta POST."""
    if request.method != "POST":
        return
    path = request.url.path
    kind = "search" if path.endswith("/me/searches") else "tailor" if _TAILOR.search(path) else "analyze" if _ANALYZE.search(path) else None
    if kind:
        check_quota(kind, user, session)