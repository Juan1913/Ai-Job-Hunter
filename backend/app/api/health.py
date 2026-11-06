"""Sondas de operación: liveness (/health), readiness (/ready) y métricas Prometheus (/metrics)."""
import hmac
import time

from fastapi import APIRouter, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session, col, func, select

from app.ai import provider
from app.ai.mode import ai_mode
from app.config import settings
from app.db import SearchRun, engine
from app.llm.client import LLMError, list_models, model_present
from app.observability import metrics

router = APIRouter(tags=["ops"])
_cache: dict[str, tuple[float, dict]] = {}


@router.get("/health")
def health():
    """Liveness: el proceso responde. No toca BD ni LLM (si no, un LLM caído reiniciaría la API)."""
    return {"ok": True}


def check_db() -> dict:
    try:
        with Session(engine) as s:
            s.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


def check_llm() -> dict:
    mode = ai_mode()
    if mode == "none":
        return {"ok": True, "mode": "none", "skipped": True}
    if mode == "api":                      # sin sonda de red (costaría tokens): configuración visible, sin clave
        info = provider.public_info()
        out = {"ok": True, "mode": "api", "provider": info["provider"], "host": info["host"], "models": [info["model_fast"], info["model_writer"]],
               "key_configured": info["key_configured"]}
        if not info["key_configured"]:
            out["warning"] = "falta JH_LLM_API_KEY: todas las funciones usan la ruta sin IA"
        return out
    wanted = sorted({settings.llm_model_fast, settings.llm_model_writer, settings.llm_model_embed} - {""})
    try:
        available = list_models()
    except LLMError as e:
        return {"ok": False, "error": f"LLM no alcanzable ({e.kind})", "missing": wanted}
    missing = [m for m in wanted if not model_present(m, available)]
    return {"ok": not missing, "mode": "local", "models": wanted, "missing": missing}


@router.get("/ready")
def ready():
    """Readiness: BD accesible y, si se exige, LLM alcanzable con todos los modelos descargados."""
    now = time.monotonic()
    hit = _cache.get("llm")
    if hit and now - hit[0] < 5:               # evita martillar a Ollama con sondas frecuentes
        llm = hit[1]
    else:
        llm = check_llm() if settings.ready_requires_llm else {"ok": True, "skipped": True}
        _cache["llm"] = (now, llm)
    checks = {"db": check_db(), "llm": llm}
    if settings.worker_external:                # con trabajador externo, sin latido reciente nada de lo lento avanza
        from app.worker import health as worker_health
        checks["worker"] = worker_health.status()
    ok = all(c["ok"] for c in checks.values())
    return JSONResponse({"ok": ok, "checks": checks}, status_code=200 if ok else 503)


def searches_running() -> int:
    with Session(engine) as s:
        return s.exec(select(func.count()).select_from(SearchRun).where(col(SearchRun.status).in_(["queued", "running"]))).one()


metrics.gauge_fn("jh_searches_running", searches_running)


@router.get("/metrics", include_in_schema=False)
def prometheus(authorization: str | None = Header(None)):
    if settings.metrics_token:
        given = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given, settings.metrics_token):
            raise HTTPException(401, "No autorizado", headers={"WWW-Authenticate": "Bearer"})
    return Response(metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")
