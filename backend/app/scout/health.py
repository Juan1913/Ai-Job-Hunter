"""Salud de las fuentes de empleo.

* `record()` la llama el servicio de búsqueda tras cada fuente (búsquedas reales).
* `check_source()` / `run_canary()` hacen una búsqueda mínima por fuente (canario en vivo).
* `get_health()` devuelve el estado persistido: pensado para que un endpoint futuro lo exponga tal cual.

Estados: ok | empty | blocked | degraded | error | unknown.
  ok       hay resultados (o un tablero de empresa sin vacantes relevantes, que es normal).
  empty    la fuente RESPONDIÓ bien y no había nada (aún dentro del umbral de ZERO_THRESHOLD búsquedas seguidas).
  blocked  429/403/captcha/circuito abierto: nos bloquean (no es lo mismo que 'vacía': hay que esperar o usar proxies).
  degraded 0 resultados repetidos sin error ni bloqueo (selector roto / bloqueo silencioso) o descripciones vacías.
  error    excepción (red, parseo, 5xx).
Los consumidores antiguos que solo conocían ok|degraded|error ven `blocked`/`empty` como estados nuevos; `get_health()` añade
`outcome` (ok|empty|blocked|error) ya normalizado.
La clave es detectar el fallo SILENCIOSO: una fuente que no lanza excepción pero devuelve 0 resultados
(selector roto, bloqueo servido como 200 vacío) queda `degraded`. Los tableros de una empresa (zero_ok)
pueden tener 0 vacantes relevantes sin que eso sea un fallo."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.domain.geo import DEFAULT_COUNTRY
from app.domain.job import Job, SearchQuery

log = logging.getLogger(__name__)

ZERO_THRESHOLD = 3       # búsquedas reales seguidas con 0 resultados para marcar degradada
STALE_AFTER = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def outcome_of(found: int, error: str | None, ev: dict | None = None) -> str:
    """ok | empty | blocked | error a partir del resultado de UNA búsqueda y los eventos HTTP del hilo (fetch.events())."""
    ev = ev or {}
    if found:
        return "ok"
    if ev.get("blocked"):
        return "blocked"
    return "error" if error else "empty"


def _classify(found: int, error: str | None, *, zero_ok: bool, canary: bool, prev_zero: int, quality: str | None = None, prev_status: str | None = None,
              blocked: str | None = None) -> tuple[str, str | None]:
    if blocked and not found:
        return "blocked", f"bloqueada: {blocked}"[:300]
    if error:
        return "error", error[:300]
    if found == 0:
        if zero_ok:
            return "ok", "0 vacantes relevantes (normal en un tablero de empresa)"
        if canary or prev_zero + 1 >= ZERO_THRESHOLD or prev_status == "degraded":
            return "degraded", "0 resultados sin error: posible selector roto o bloqueo silencioso"
        return "empty", f"0 resultados ({prev_zero + 1}/{ZERO_THRESHOLD} antes de marcar degradada)"
    if quality:
        return "degraded", quality
    return "ok", None


def quality_issue(jobs: list[Job]) -> str | None:
    """Hay resultados pero vacíos: la mayoría sin descripción o sin empresa = el parseo del detalle se rompió."""
    if len(jobs) < 2:
        return None
    no_desc = sum(1 for j in jobs if len(j.description) < 80)
    if no_desc / len(jobs) > 0.5:
        return f"{no_desc}/{len(jobs)} ofertas sin descripción útil: el parseo del detalle parece roto"
    return None


def record(source: str, found: int, error: str | None, elapsed: float, *, zero_ok: bool = False, canary: bool = False,
           quality: str | None = None, blocked: str | None = None, session: Session | None = None) -> None:
    """Upsert en SourceHealth. Nunca lanza: la telemetría no puede romper una búsqueda."""
    try:
        from app.db import SourceHealth, engine
        own = session is None
        s = session or Session(engine)
        try:
            row = s.get(SourceHealth, source) or SourceHealth(source=source)
            status, note = _classify(found, error, zero_ok=zero_ok, canary=canary, prev_zero=row.consecutive_zero or 0, quality=quality, prev_status=row.status, blocked=blocked)
            now = _now()
            row.runs = (row.runs or 0) + 1
            row.status, row.note, row.last_found, row.last_error = status, note, found, (error[:500] if error else None)
            row.last_latency_ms, row.last_run_at, row.updated_at = int(elapsed * 1000), now, now
            row.consecutive_errors = (row.consecutive_errors or 0) + 1 if (error or status == "blocked") else 0
            if status == "blocked" and not row.last_error:
                row.last_error = (blocked or "")[:500]
            row.consecutive_zero = 0 if (found or error or status == "blocked") else (row.consecutive_zero or 0) + 1
            if status == "ok" and found:
                row.last_ok_at = now
            if canary:
                row.last_canary_at = now
            s.add(row)
            s.commit()
        finally:
            if own:
                s.close()
    except Exception as e:  # noqa: BLE001
        log.debug("no se pudo registrar la salud de %s: %s", source, e)


def get_health(session: Session | None = None) -> list[dict]:
    """Estado persistido de todas las fuentes (listo para serializar en un endpoint)."""
    from app.db import SourceHealth, engine
    own = session is None
    s = session or Session(engine)
    try:
        rows = s.exec(select(SourceHealth).order_by(SourceHealth.source)).all()
        now = _now()
        out = []
        for r in rows:
            last = r.last_run_at.replace(tzinfo=timezone.utc) if r.last_run_at and r.last_run_at.tzinfo is None else r.last_run_at
            out.append({"source": r.source, "status": r.status, "outcome": {"ok": "ok", "degraded": "empty", "empty": "empty", "blocked": "blocked", "error": "error"}.get(r.status, "unknown"),
                        "stale": bool(last and now - last > STALE_AFTER), "last_found": r.last_found,
                        "last_error": r.last_error, "note": r.note, "consecutive_zero": r.consecutive_zero,
                        "consecutive_errors": r.consecutive_errors, "runs": r.runs, "latency_ms": r.last_latency_ms,
                        "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
                        "last_ok_at": r.last_ok_at.isoformat() if r.last_ok_at else None,
                        "last_canary_at": r.last_canary_at.isoformat() if r.last_canary_at else None})
        return out
    finally:
        if own:
            s.close()


# ---------------------------------------------------------------- canario
def canary_query(source) -> SearchQuery:
    """Búsqueda mínima y genérica: pocas ofertas, término muy común en el idioma/país de la fuente."""
    countries = sorted(getattr(source, "countries", set()) or [])
    if getattr(source, "group", "") in ("ats", "careers"):
        return SearchQuery(keywords=[], limit_per_source=3)            # sin palabra clave: cualquier vacante sirve
    if countries:
        cc = DEFAULT_COUNTRY if DEFAULT_COUNTRY in countries else countries[0]
        return SearchQuery(keywords=["analista"], countries=[cc], limit_per_source=3)
    return SearchQuery(keywords=["developer"], limit_per_source=3)


def check_source(source, query: SearchQuery | None = None, *, persist: bool = True) -> dict:
    """Una búsqueda mínima en vivo. Devuelve {source, status, found, seconds, error, note}."""
    from app.scout import fetch
    from app.scout.normalize import finalize
    q = query or canary_query(source)
    zero_ok = getattr(source, "zero_ok", False)
    t0 = time.monotonic()
    jobs: list[Job] = []
    error = None
    fetch.reset_events()
    try:
        jobs = [finalize(j) for j in source.search(q)]
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    finally:
        fetch.close_session()
    elapsed = time.monotonic() - t0
    ev = fetch.events()
    outcome = outcome_of(len(jobs), error, ev)
    blocked = (error or ev["last_blocked"] or "bloqueada") if outcome == "blocked" else None
    quality = quality_issue(jobs) if not error else None
    status, note = _classify(len(jobs), error, zero_ok=zero_ok, canary=True, prev_zero=0, quality=quality, blocked=blocked)
    if persist:
        record(source.name, len(jobs), error, elapsed, zero_ok=zero_ok, canary=True, quality=quality, blocked=blocked)
    return {"source": source.name, "status": status, "outcome": outcome, "found": len(jobs), "seconds": round(elapsed, 1), "error": error, "note": note,
            "sample": jobs[0].title if jobs else None}


def run_canary(sources: list | None = None, *, only: list[str] | None = None, persist: bool = True, max_workers: int = 3) -> list[dict]:
    """Canario sobre todas las fuentes (o las de `only`). Reutilizable por un endpoint futuro."""
    if sources is None:
        from app.scout.registry import build_sources
        sources = build_sources()
    if only:
        sources = [s for s in sources if any(s.name == o or s.name.startswith(o) for o in only)]
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="canary") as ex:
        return list(ex.map(lambda s: check_source(s, persist=persist), sources))


def format_table(results: list[dict]) -> str:
    icon = {"ok": "OK", "degraded": "DEGRADADA", "error": "ERROR", "blocked": "BLOQUEADA", "empty": "VACÍA"}
    rows = [("fuente", "estado", "n", "seg", "detalle")]
    for r in results:
        rows.append((r["source"], icon.get(r["status"], r["status"]), str(r["found"]), str(r["seconds"]),
                     (r["error"] or r["note"] or r.get("sample") or "")[:90]))
    widths = [max(len(row[i]) for row in rows) for i in range(5)]
    return "\n".join("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)) for row in rows)
