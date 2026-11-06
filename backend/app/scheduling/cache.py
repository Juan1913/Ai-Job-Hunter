"""Caché COMPARTIDA entre usuarios de los resultados crudos de scraping.

Clave: `v{VERSION}:{fuente}:{país(es)}:{hash}`, donde el hash cubre la consulta NORMALIZADA (minúsculas, sin tildes, palabras clave y variantes
ordenadas) y todo lo que cambia lo que la fuente devuelve (regiones, idiomas, remoto, límite, antigüedad, filtros, offset, detalle). Dos usuarios
con la misma consulta, país y filtros comparten entrada; si difiere cualquier filtro no se comparte (preferimos un fallo de caché a servir algo
distinto de lo pedido).

Qué se comparte: SOLO lo que el portal devolvió (Job.model_dump()). La evaluación, el puntaje y la deduplicación por usuario ocurren después,
siempre por usuario, en el runner.

Invalidación: (1) TTL (`cache_ttl_s`; vacío más corto; por fuente con `cache_ttl_overrides`); (2) `VERSION`: súbela si cambia la normalización o el
esquema de Job y toda la caché anterior deja de usarse; (3) `invalidate(source=..., country=...)` a mano o por operador; (4) lo que sale de una
fuente BLOQUEADA o con error nunca se guarda; (5) `purge()` borra lo caducado.

Un solo vuelo: dentro del proceso, dos hilos que piden la misma clave a la vez esperan al primero en vez de duplicar la petición."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from app.config import settings
from app.db import SourceCache, engine, utcnow
from app.domain.job import Job, SearchQuery
from app.observability import metrics
from app.scheduling import budget
from app.text import norm

log = logging.getLogger(__name__)
VERSION = 1
MAX_ROWS = 5000
_flight_lock = threading.Lock()
_flights: dict[str, threading.Lock] = {}


def _words(items: list[str]) -> list[str]:
    return sorted({norm(x) for x in items if x and x.strip()})


def query_fingerprint(q: SearchQuery) -> dict:
    return {"kw": _words(q.keywords), "var": _words(q.variants), "c": sorted(c.upper() for c in q.countries), "r": _words(q.regions),
            "l": sorted(x.lower() for x in q.languages), "remote": q.remote, "lim": q.limit_per_source, "hours": q.hours_old,
            "et": sorted(q.employment_types), "wm": sorted(q.work_modes), "strict": q.strict_filters, "off": q.offset, "desc": q.fetch_description,
            "budget": q.request_budget}


def cache_key(source: str, q: SearchQuery) -> tuple[str, str, str]:
    """(clave, país, consulta legible)."""
    fp = query_fingerprint(q)
    digest = hashlib.sha1(json.dumps(fp, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    country = "-".join(fp["c"]) or "ANY"
    return f"v{VERSION}:{source}:{country}:{digest}", country, " | ".join(fp["kw"])


def ttl_for(source: str, empty: bool) -> int:
    if empty:
        return settings.cache_empty_ttl_s
    for part in settings.cache_ttl_overrides.split(","):
        k, _, v = part.partition("=")
        if k.strip() == source and v.strip().isdigit():
            return int(v)
    return settings.cache_ttl_s


def get(key: str, now: datetime | None = None) -> list[Job] | None:
    now = now or utcnow()
    with Session(engine) as s:
        row = s.get(SourceCache, key)
        if row is None or budget.aware(row.expires_at) <= now:
            return None
        row.hits += 1
        s.add(row)
        s.commit()
        return [Job.model_validate(d) for d in row.payload]


def put(key: str, source: str, country: str, text: str, jobs: list[Job], now: datetime | None = None) -> None:
    now = now or utcnow()
    ttl = ttl_for(source, empty=not jobs)
    if ttl <= 0:
        return
    payload = [j.model_dump() for j in jobs]
    with Session(engine) as s:
        row = s.get(SourceCache, key) or SourceCache(key=key, source=source)
        row.country, row.query_text, row.payload, row.n = country, text[:200], payload, len(payload)
        row.created_at, row.expires_at, row.hits = now, now + timedelta(seconds=ttl), 0
        s.add(row)
        try:
            s.commit()
        except Exception:  # noqa: BLE001  carrera: otro trabajador insertó la misma clave; la suya vale igual
            s.rollback()


def invalidate(*, source: str | None = None, country: str | None = None, key: str | None = None) -> int:
    with Session(engine) as s:
        stmt = select(SourceCache)
        if key:
            stmt = stmt.where(SourceCache.key == key)
        if source:
            stmt = stmt.where(SourceCache.source == source)
        if country:
            stmt = stmt.where(col(SourceCache.country).contains(country.upper()))
        rows = s.exec(stmt).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return len(rows)


def purge(now: datetime | None = None) -> int:
    """Borra lo caducado y, si la tabla crece de más, lo más antiguo."""
    now = now or utcnow()
    with Session(engine) as s:
        rows = s.exec(select(SourceCache).where(col(SourceCache.expires_at) <= now)).all()
        extra = s.exec(select(SourceCache).order_by(col(SourceCache.created_at).desc()).offset(MAX_ROWS)).all()
        doomed = {r.key: r for r in (*rows, *extra)}
        for r in doomed.values():
            s.delete(r)
        s.commit()
    return len(doomed)


def _flight(key: str) -> threading.Lock:
    with _flight_lock:
        return _flights.setdefault(key, threading.Lock())


class SharedCacheSource:
    """Envuelve una fuente: sirve de la caché compartida si hay una entrada vigente y, si no, busca de verdad (contando peticiones)."""

    def __init__(self, inner, *, use_cache: bool = True, clock=None):
        self._inner, self._use_cache = inner, use_cache
        self._clock = clock or (lambda: utcnow())      # se resuelve al llamar: los tests pueden inyectar un reloj simulado
        self.name = inner.name
        self.countries = getattr(inner, "countries", set())

    def __getattr__(self, item):                 # remote_only, native_offset, zero_ok, ...
        return getattr(self._inner, item)

    def search(self, query: SearchQuery) -> list[Job]:
        key, country, text = cache_key(self.name, query)
        if self._use_cache:
            hit = get(key, self._clock())
            if hit is not None:
                metrics.inc("jh_source_cache_total", result="hit")
                return hit
            with _flight(key):                   # un solo vuelo por clave
                hit = get(key, self._clock())
                if hit is not None:
                    metrics.inc("jh_source_cache_total", result="hit")
                    return hit
                return self._fetch(query, key, country, text)
        return self._fetch(query, None, country, text)

    def _fetch(self, query: SearchQuery, key: str | None, country: str, text: str) -> list[Job]:
        from app.scout import fetch
        metrics.inc("jh_source_cache_total", result="miss" if key else "bypass")
        with budget.counting(self.name):
            jobs = self._inner.search(query)
            ev = fetch.events()
        silent_block = ev.get("blocked") and not ev.get("ok")
        if key and not silent_block:             # lo bloqueado (aunque la fuente lo disfrace de lista vacía) nunca se comparte
            put(key, self.name, country, text, jobs, self._clock())
        return jobs


def wrap_sources(sources: list, *, use_cache: bool, now: datetime | None = None) -> tuple[list, dict[str, dict]]:
    """Envuelve las fuentes y omite las que no tienen presupuesto. Devuelve (fuentes a ejecutar, {fuente: info} de las omitidas)."""
    now = now or utcnow()
    run: list = []
    skipped: dict[str, dict] = {}
    for src in sources:
        reason = budget.blocked_reason(src.name, now)
        if reason:
            skipped[src.name] = {"status": "skipped", "found": 0, "error": reason, "outcome": "budget"}
            metrics.inc("jh_budget_skipped_total")
            continue
        run.append(SharedCacheSource(src, use_cache=use_cache))
    return run, skipped
