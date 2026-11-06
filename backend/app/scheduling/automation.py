"""Automatización por política del usuario tras cada búsqueda. Evalúa, avisa y, si el usuario lo pidió, deja listo el CV adaptado para que él decida.
Postular solo ocurre si el usuario activó a propósito la auto-postulación (app/autoapply: apagada por defecto, en ensayo al empezar, con aprobación o automática).

Pasos, en este orden y todos idempotentes (un reintento tras una caída no duplica nada):
  1. contabilidad de la búsqueda programada (racha de fallos, backoff, pausa);
  2. evaluar las ofertas NUEVAS de la ejecución (determinista, sin IA) si `auto_evaluate`;
  3. avisar de las que superan `min_score` (agrupado por ejecución, o al resumen diario/semanal);
  4. si `auto_tailor`: encolar la adaptación del CV SOLO para las que recomiendan `apply`, dentro del presupuesto diario del usuario."""
from __future__ import annotations

import importlib.util
import logging
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from app.ai.mode import ai_available
from app.config import settings
from app.db import JobRow, JobTask, ProfileRow, SearchRun, TailoredRow, User, engine, utcnow
from app.domain.profile import Profile
from app.evaluation import service as evaluation
from app.notifications import service as notif
from app.scheduling import service as sched

log = logging.getLogger(__name__)
_EXTRACTIVE_MODULES = ("app.ai.writer", "app.cv.extractive", "app.ai.extractive", "app.cv.tailor_extractive")   # dónde viviría el modo `none` extractivo


def extractive_tailor_exists() -> bool:
    for m in _EXTRACTIVE_MODULES:
        try:
            if importlib.util.find_spec(m) is not None:
                return True
        except (ImportError, ValueError):
            continue
    return False


def tailor_possible() -> bool:
    """¿Se puede pre-generar un CV ahora? Con IA sí; sin IA solo si existe el modo extractivo (o el operador lo fuerza con JH_WORKER_PREGEN_WITHOUT_AI)."""
    if ai_available():
        return True
    if settings.worker_pregen_without_ai is not None:
        return settings.worker_pregen_without_ai
    return extractive_tailor_exists()


def _recommendation(row: JobRow) -> str | None:
    ev = row.evaluation or {}
    return ev.get("recommendation") if isinstance(ev, dict) else None


def _vetoed(row: JobRow) -> bool:
    ev = row.evaluation or {}
    return bool(isinstance(ev, dict) and ev.get("vetoes"))


def tailor_budget_left(s: Session, user_id: int, daily: int, now: datetime) -> int:
    since = now - timedelta(hours=24)
    used = s.exec(select(JobTask).where(JobTask.user_id == user_id, JobTask.kind == "auto_tailor", col(JobTask.created_at) >= since)).all()
    return max(0, daily - len(used))


def queue_tailors(s: Session, user: User, rows: list[JobRow], run_id: int, prefs, now: datetime, llm=None, writer=None) -> int:
    """Encola `auto_tailor` para las ofertas `apply` hasta agotar el presupuesto diario. Devuelve cuántas encoló."""
    if not tailor_possible():
        return 0
    left = tailor_budget_left(s, user.id, prefs.tailor_daily_budget, now)
    if left <= 0:
        return 0
    from fastapi import HTTPException

    from app.api.deps import check_quota
    from app.llm.factory import build_llm
    from app.services.tasks import start_task
    llm, writer = llm or build_llm("fast", user.id), writer or build_llm("writer", user.id)
    n = 0
    for row in rows:
        if n >= left:
            break
        if s.exec(select(TailoredRow.id).where(TailoredRow.user_id == user.id, TailoredRow.job_id == row.id)).first():
            continue
        try:
            check_quota("tailor", user, s)
        except HTTPException:
            break                      # la cuota diaria del sistema también manda
        start_task(s, user, row.id, "auto_tailor", {"auto": True, "run_id": run_id}, llm, writer)
        n += 1
    return n


def _autoapply(s: Session, run: SearchRun) -> dict:
    """Auto-postulación opcional: SOLO si el usuario la configuró (modo approval/auto). Nunca rompe el resto de la automatización."""
    try:
        from app.autoapply import service as aa
        user = s.get(User, run.user_id)
        rows = list(s.exec(select(JobRow).where(JobRow.user_id == run.user_id, JobRow.run_id == run.id)).all())
        return aa.on_new_jobs(s, user, rows) if user and rows else {}
    except Exception:  # noqa: BLE001
        log.exception("auto-postulación de la búsqueda %s falló", run.id)
        s.rollback()
        return {}


def after_run(run_id: int, *, now: datetime | None = None, llm=None, writer=None) -> dict:
    now = now or utcnow()
    res = {"notified": 0, "evaluated": 0, "tailors": 0}
    with Session(engine) as s:
        run = s.get(SearchRun, run_id)
        if run is None:
            return res
        ss_id = (run.params or {}).get("saved_search_id")
        name = ""
        if ss_id:
            ss = sched.record_result(s, ss_id, run, now)
            name = ss.name if ss else ""
        if run.status != "done":
            return res
        res["autoapply"] = _autoapply(s, run)             # política propia del usuario (apagada por defecto); no depende de los avisos
        prefs = notif.get_prefs(s, run.user_id)
        if not (ss_id or prefs.apply_to_manual):
            return res
        user = s.get(User, run.user_id)
        prow = s.get(ProfileRow, run.user_id)
        rows = list(s.exec(select(JobRow).where(JobRow.user_id == run.user_id, JobRow.run_id == run.id)).all())
        if not rows or user is None or prow is None:
            return res
        if prefs.auto_evaluate:
            profile = Profile.model_validate(prow.data)
            evaluation.evaluate_many(s, rows, profile)          # sin llm: determinista; falla de un informe no debe impedir los avisos
            res["evaluated"] = len(rows)
        scored = [(r, sc) for r in rows if not _vetoed(r) and (sc := notif.job_score(r)) is not None and sc >= prefs.min_score]
        n = notif.notify_new_jobs(s, run.user_id, run.id, name, scored, prefs, now)
        res["notified"] = len(scored) if (n or prefs.digest != "off") else 0
        if prefs.auto_tailor and prefs.auto_evaluate:
            apply_rows = [r for r, _ in sorted(scored, key=lambda x: -x[1]) if _recommendation(r) == "apply"]
            res["tailors"] = queue_tailors(s, user, apply_rows, run.id, prefs, now, llm, writer)
    return res
