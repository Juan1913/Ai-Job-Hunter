"""Presupuesto global de peticiones salientes a portales, por hora y por dominio.

Cuenta las peticiones REALES (las que pasan por el rate limiter de app/scout/ratelimit.py, que no se edita: aquí se envuelve `limiter.acquire`
en la instancia) y las persiste por hora en BD, así el tope vale para todos los trabajadores y reinicios. Antes de lanzar una fuente se mira si
su(s) dominio(s) o el global están agotados; en ese caso la fuente se OMITE en esa ejecución (no se le pregunta, así no ensucia su salud)."""
from __future__ import annotations

import threading
from collections import Counter
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.config import settings
from app.db import RequestBudget, engine, utcnow
from app.observability import metrics
from app.scheduling.windows import aware
from app.scout import ratelimit

_tls = threading.local()
_source_hosts: dict[str, set[str]] = {}      # fuente -> dominios que ha tocado (se aprende al ejecutarla)
_lock = threading.Lock()
_installed = False


def hour_key(now: datetime) -> str:
    return aware(now).strftime("%Y-%m-%dT%H")


def _bucket(now: datetime, scope: str) -> str:
    return f"{hour_key(now)}|{scope}"


def _install() -> None:
    """Envuelve limiter.acquire una sola vez: cada petición pasa por aquí y se anota en el recolector del hilo si lo hay."""
    global _installed
    with _lock:
        if _installed:
            return
        orig = ratelimit.limiter.acquire

        def counted(url_or_host: str) -> float:
            col_ = getattr(_tls, "counter", None)
            if col_ is not None:
                host = ratelimit.host_of(url_or_host)
                col_[host] += 1
                src = getattr(_tls, "source", None)
                if src:
                    _source_hosts.setdefault(src, set()).add(host)
            return orig(url_or_host)

        ratelimit.limiter.acquire = counted      # type: ignore[method-assign]
        _installed = True


@contextmanager
def counting(source: str):
    """Recoge las peticiones que haga ESTE hilo mientras la fuente busca y las persiste al salir."""
    _install()
    counter: Counter = Counter()
    _tls.counter, _tls.source = counter, source
    try:
        yield counter
    finally:
        _tls.counter = _tls.source = None
        if counter:
            record(dict(counter))


def record(host_counts: dict[str, int], now: datetime | None = None) -> None:
    now = now or utcnow()
    scopes = {"global": sum(host_counts.values()), **{f"domain:{h}": n for h, n in host_counts.items()}}
    with Session(engine) as s:
        for scope, n in scopes.items():
            b = _bucket(now, scope)
            if s.execute(update(RequestBudget).where(RequestBudget.bucket == b).values(count=RequestBudget.count + n)).rowcount == 0:
                try:
                    with s.begin_nested():
                        s.add(RequestBudget(bucket=b, hour=hour_key(now), scope=scope, count=n))
                except Exception:  # noqa: BLE001  otro trabajador la creó entre medias
                    s.execute(update(RequestBudget).where(RequestBudget.bucket == b).values(count=RequestBudget.count + n))
        s.commit()
    metrics.inc("jh_outbound_requests_total", sum(host_counts.values()))


def used(scope: str, now: datetime | None = None) -> int:
    now = now or utcnow()
    with Session(engine) as s:
        row = s.get(RequestBudget, _bucket(now, scope))
        return row.count if row else 0


def blocked_reason(source: str, now: datetime | None = None) -> str | None:
    """None = puede ejecutarse; si no, por qué se omite. Los dominios de la fuente se conocen tras su primera ejecución en este proceso."""
    now = now or utcnow()
    if settings.budget_global_per_hour and used("global", now) >= settings.budget_global_per_hour:
        return f"presupuesto global agotado ({settings.budget_global_per_hour}/h)"
    if settings.budget_domain_per_hour:
        for host in sorted(_source_hosts.get(source, ())):
            if used(f"domain:{host}", now) >= settings.budget_domain_per_hour:
                return f"presupuesto de {host} agotado ({settings.budget_domain_per_hour}/h)"
    return None


def purge(now: datetime | None = None, keep_hours: int = 48) -> int:
    from datetime import timedelta
    cutoff = hour_key(aware(now or utcnow()) - timedelta(hours=keep_hours))
    with Session(engine) as s:
        rows = s.exec(select(RequestBudget).where(col(RequestBudget.hour) < cutoff)).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return len(rows)
