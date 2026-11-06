import inspect
import logging
import time
from concurrent.futures import ThreadPoolExecutor, wait

from app.domain.job import Job, SearchQuery
from app.safety.integrity import gate_job
from app.scout import fetch, health
from app.scout.base import Source, supports
from app.scout.normalize import finalize, matches_filters
from app.scout.query import expand_query

log = logging.getLogger(__name__)

MAX_WORKERS = 6
SEARCH_DEADLINE = 900   # s: tope total; una fuente colgada no bloquea al resto ni a la búsqueda


def prepare(jobs: list[Job], query: SearchQuery) -> list[Job]:
    """Normaliza (fecha, salario, idioma, modo, país) y descarta ofertas cerradas o fuera del idioma pedido."""
    default_country = query.countries[0] if len(query.countries) == 1 else None
    out = []
    for j in jobs:
        finalize(j, default_country=default_country)
        gate_job(j)                      # portón de integridad: texto ajeno saneado y oferta marcada si hay hallazgos
        if j.closed:
            continue
        if query.languages and j.language and j.language not in query.languages:
            continue
        if not matches_filters(j, query):      # antigüedad / contrato / modalidad (filtro local, siempre)
            continue
        out.append(j)
    return out


def search_source(s: Source, query: SearchQuery) -> list[Job]:
    """Una fuente con `offset`: nativo si la fuente lo declara (`native_offset`), si no se pide offset+límite y se salta el inicio."""
    if query.offset <= 0 or getattr(s, "native_offset", False):
        return s.search(query)
    wide = query.model_copy(update={"limit_per_source": query.limit_per_source + query.offset, "offset": 0})
    return s.search(wide)[query.offset:]


def _notify(cb, name: str, found: int, err: str | None, outcome: str) -> None:
    """`on_source(name, found, err)` de siempre; si el callback acepta un 4.º argumento recibe también el resultado."""
    if not cb:
        return
    try:
        ps = inspect.signature(cb).parameters.values()
        wide = any(p.kind is p.VAR_POSITIONAL for p in ps) or len(list(ps)) >= 4
    except (TypeError, ValueError):
        wide = False
    cb(name, found, err, outcome) if wide else cb(name, found, err)


def run_search(query: SearchQuery, sources: list[Source], on_source=None, report: dict | None = None) -> tuple[list[Job], dict[str, str]]:
    """Busca en paralelo; un portal caído no afecta al resto. Devuelve (ofertas, errores por fuente).
    `report` (opcional) se rellena con {fuente: {outcome: ok|empty|blocked|error, raw, found, seconds, blocked, note}}:
    'blocked' (429/403/captcha/circuito abierto) se distingue de 'empty' (la fuente respondió bien y no había nada)."""
    errors: dict[str, str] = {}
    rep = report if report is not None else {}
    try:
        query = expand_query(query)        # variantes ES/EN y sinónimos del rol (diccionario; LLM solo si está activado)
    except Exception as e:  # noqa: BLE001 — la expansión es un extra: nunca tumba la búsqueda
        log.warning("no se pudo expandir la consulta: %s", e)

    def one(s: Source) -> list[Job]:
        t0 = time.monotonic()
        fetch.reset_events()
        zero_ok = getattr(s, "zero_ok", False)
        try:
            raw = search_source(s, query)
            found = prepare(raw, query)
            ev = fetch.events()
            outcome = health.outcome_of(len(raw), None, ev)
            blocked = ev["last_blocked"] or "bloqueada" if outcome == "blocked" else None
            elapsed = time.monotonic() - t0
            health.record(s.name, len(raw), None, elapsed, zero_ok=zero_ok, blocked=blocked)
            rep[s.name] = {"outcome": outcome, "raw": len(raw), "found": len(found), "seconds": round(elapsed, 1), "blocked": ev["blocked"],
                           "requests": ev["requests"], "error": None,
                           "sample": [{"title": j.title, "company": j.company, "location": j.location, "remote": j.remote} for j in found[:6]]}
            _notify(on_source, s.name, len(found), None, outcome)
            return found
        except Exception as e:  # noqa: BLE001
            ev = fetch.events()
            outcome = "blocked" if isinstance(e, fetch.Blocked) or (ev["blocked"] and not ev["ok"]) else "error"
            errors[s.name] = str(e)
            log.warning("fuente %s %s: %s", s.name, "bloqueada" if outcome == "blocked" else "falló", e)
            elapsed = time.monotonic() - t0
            health.record(s.name, 0, str(e), elapsed, zero_ok=zero_ok, blocked=str(e) if outcome == "blocked" else None)
            rep[s.name] = {"outcome": outcome, "raw": 0, "found": 0, "seconds": round(elapsed, 1), "blocked": ev["blocked"], "error": str(e)[:300]}
            _notify(on_source, s.name, 0, str(e), outcome)
            return []
        finally:
            fetch.close_session()      # cierra el navegador del hilo si la fuente usó StealthyFetcher

    active = [s for s in sources if supports(s, query)]
    ex = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="scout")
    futures = {ex.submit(one, s): s for s in active}
    done, pending = wait(futures, timeout=SEARCH_DEADLINE)
    for f in pending:
        name = futures[f].name
        errors[name] = "tiempo de espera agotado"
        health.record(name, 0, "tiempo de espera agotado", SEARCH_DEADLINE, zero_ok=True)
        rep[name] = {"outcome": "error", "raw": 0, "found": 0, "seconds": SEARCH_DEADLINE, "blocked": 0, "error": "tiempo de espera agotado"}
        _notify(on_source, name, 0, "tiempo de espera agotado", "error")
    ex.shutdown(wait=False, cancel_futures=True)

    unique: dict[str, Job] = {}
    for f in futures:                    # en el orden de las fuentes (gana la primera en caso de duplicado)
        if f in done:
            for j in f.result():
                unique.setdefault(j.dedup_key, j)
    return list(unique.values()), errors
