"""Cola persistente en BD con lease. Un trabajo reclamado pertenece a un trabajador hasta `locked_until`; mientras vive lo renueva.
Si el trabajador cae, el lease vence y otro lo recupera (`recover_orphans`). Tras `max_attempts` pasa a `dead` (dead-letter).

Exclusión mutua entre trabajadores:
  - PostgreSQL: `SELECT ... FOR UPDATE SKIP LOCKED` (cada trabajador salta lo que otro ya está tomando).
  - SQLite: `BEGIN IMMEDIATE` (un único escritor a la vez; el que llega después ve el lease ya puesto).
Además cada transición es un UPDATE condicionado al dueño (`locked_by`), así un trabajador que perdió el lease no pisa a quien lo recuperó."""
from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, update
from sqlmodel import Session, col, func, select

from app.config import settings
from app.db import WorkItem, engine, utcnow

log = logging.getLogger(__name__)
ACTIVE = ("queued", "running")


@dataclass
class Claimed:
    """Copia desligada de la sesión de un trabajo reclamado."""
    id: int
    kind: str
    ref_id: int | None
    user_id: int | None
    payload: dict
    attempts: int
    max_attempts: int
    priority: int = 0
    extra: dict = field(default_factory=dict)


def _is_sqlite() -> bool:
    return engine.dialect.name == "sqlite"


def _is_pg() -> bool:
    return engine.dialect.name == "postgresql"


def backoff_s(attempt: int, base: float | None = None, cap: float | None = None, rng: Callable[[], float] = random.random) -> float:
    """Espera antes del reintento `attempt` (1 = tras el primer fallo): base * 2^(n-1), con tope y +-20 % de jitter."""
    base = settings.worker_backoff_s if base is None else base
    cap = settings.worker_backoff_max_s if cap is None else cap
    return min(cap, base * 2 ** max(0, attempt - 1)) * (0.8 + 0.4 * rng())


def enqueue(session: Session, kind: str, *, ref_id: int | None = None, user_id: int | None = None, payload: dict | None = None,
            priority: int = 0, run_after: datetime | None = None, max_attempts: int | None = None, dedup_key: str | None = None,
            now: datetime | None = None, commit: bool = True) -> WorkItem:
    """Encola un trabajo. Con `dedup_key`, si ya hay uno activo con esa clave devuelve ese (idempotente)."""
    if dedup_key:
        prev = session.exec(select(WorkItem).where(WorkItem.dedup_key == dedup_key, col(WorkItem.state).in_(ACTIVE))).first()
        if prev:
            return prev
    item = WorkItem(kind=kind, ref_id=ref_id, user_id=user_id, payload=payload or {}, priority=priority, dedup_key=dedup_key,
                    run_after=run_after or now or utcnow(), max_attempts=max_attempts or settings.worker_max_attempts)
    session.add(item)
    if commit:
        session.commit()
        session.refresh(item)
    else:
        session.flush()
    return item


def claim(worker_id: str, *, limit: int = 1, kinds: list[str] | None = None, now: datetime | None = None, lease_s: float | None = None) -> list[Claimed]:
    """Reclama hasta `limit` trabajos listos (por prioridad, luego antigüedad). Atómico entre trabajadores y procesos."""
    now = now or utcnow()
    until = now + timedelta(seconds=lease_s if lease_s is not None else settings.worker_lease_s)
    out: list[Claimed] = []
    with Session(engine) as s:
        if _is_sqlite():
            s.connection().exec_driver_sql("BEGIN IMMEDIATE")     # toma el bloqueo de escritura antes de leer candidatos
        stmt = select(WorkItem).where(WorkItem.state == "queued", col(WorkItem.run_after) <= now)
        if kinds:
            stmt = stmt.where(col(WorkItem.kind).in_(kinds))
        stmt = stmt.order_by(col(WorkItem.priority).desc(), col(WorkItem.run_after), col(WorkItem.id)).limit(limit)
        if _is_pg():
            stmt = stmt.with_for_update(skip_locked=True)
        for it in s.exec(stmt).all():
            it.state, it.locked_by, it.locked_until = "running", worker_id, until
            it.attempts += 1
            it.started_at = it.started_at or now
            s.add(it)
            out.append(Claimed(it.id, it.kind, it.ref_id, it.user_id, dict(it.payload or {}), it.attempts, it.max_attempts, it.priority))
        s.commit()
    return out


def extend(item_id: int, worker_id: str, *, now: datetime | None = None, lease_s: float | None = None) -> bool:
    """Renueva el lease. False = lo perdimos (otro trabajador lo recuperó): el resultado de este trabajo ya no cuenta."""
    now = now or utcnow()
    with Session(engine) as s:
        n = s.execute(update(WorkItem).where(WorkItem.id == item_id, WorkItem.locked_by == worker_id, WorkItem.state == "running")
                      .values(locked_until=now + timedelta(seconds=lease_s if lease_s is not None else settings.worker_lease_s))).rowcount
        s.commit()
    return n == 1


def complete(item_id: int, worker_id: str, *, now: datetime | None = None) -> bool:
    with Session(engine) as s:
        n = s.execute(update(WorkItem).where(WorkItem.id == item_id, WorkItem.locked_by == worker_id, WorkItem.state == "running")
                      .values(state="done", locked_by=None, locked_until=None, finished_at=now or utcnow(), last_error=None)).rowcount
        s.commit()
    return n == 1


def fail(item: Claimed, worker_id: str, error: str, *, retry: bool = True, now: datetime | None = None,
         rng: Callable[[], float] = random.random) -> str | None:
    """Falla un intento: reencola con backoff exponencial o, agotados los intentos (o `retry=False`), dead-letter.
    Devuelve 'queued' | 'dead' | None (perdimos el lease)."""
    now = now or utcnow()
    dead = (not retry) or item.attempts >= item.max_attempts
    values = ({"state": "dead", "finished_at": now} if dead else
              {"state": "queued", "run_after": now + timedelta(seconds=backoff_s(item.attempts, rng=rng))})
    with Session(engine) as s:
        n = s.execute(update(WorkItem).where(WorkItem.id == item.id, WorkItem.locked_by == worker_id, WorkItem.state == "running")
                      .values(locked_by=None, locked_until=None, last_error=error[:500], **values)).rowcount
        s.commit()
    return None if n != 1 else ("dead" if dead else "queued")


def defer(item: Claimed, worker_id: str, seconds: float, reason: str = "", *, now: datetime | None = None) -> bool:
    """Aplaza SIN gastar un intento (límite por hora, silencio nocturno)."""
    now = now or utcnow()
    with Session(engine) as s:
        n = s.execute(update(WorkItem).where(WorkItem.id == item.id, WorkItem.locked_by == worker_id, WorkItem.state == "running")
                      .values(state="queued", locked_by=None, locked_until=None, attempts=max(0, item.attempts - 1),
                              run_after=now + timedelta(seconds=seconds), last_error=reason[:500] or None)).rowcount
        s.commit()
    return n == 1


def release(item_id: int, worker_id: str, *, now: datetime | None = None) -> bool:
    """Apagado ordenado: devuelve el trabajo a la cola sin contar el intento."""
    with Session(engine) as s:
        n = s.execute(update(WorkItem).where(WorkItem.id == item_id, WorkItem.locked_by == worker_id, WorkItem.state == "running")
                      .values(state="queued", locked_by=None, locked_until=None, run_after=now or utcnow(),
                              attempts=func.max(WorkItem.attempts - 1, 0) if _is_sqlite() else func.greatest(WorkItem.attempts - 1, 0))).rowcount
        s.commit()
    return n == 1


def recover_orphans(*, now: datetime | None = None) -> tuple[list[int], list[Claimed]]:
    """Trabajos `running` con lease vencido (el trabajador murió o se colgó): vuelven a la cola, o a dead si agotaron intentos.
    Devuelve (ids reencolados, trabajos muertos) para que quien llama notifique el dead-letter."""
    now = now or utcnow()
    requeued: list[int] = []
    dead: list[Claimed] = []
    with Session(engine) as s:
        if _is_sqlite():
            s.connection().exec_driver_sql("BEGIN IMMEDIATE")
        stmt = select(WorkItem).where(WorkItem.state == "running", or_(col(WorkItem.locked_until) < now, col(WorkItem.locked_until) == None))   # noqa: E711
        if _is_pg():
            stmt = stmt.with_for_update(skip_locked=True)
        for it in s.exec(stmt).all():
            it.locked_by, it.locked_until = None, None
            if it.attempts >= it.max_attempts:
                it.state, it.finished_at, it.last_error = "dead", now, "lease vencido tras agotar los intentos"
                dead.append(Claimed(it.id, it.kind, it.ref_id, it.user_id, dict(it.payload or {}), it.attempts, it.max_attempts, it.priority))
            else:
                it.state, it.run_after, it.last_error = "queued", now, "lease vencido: trabajador caído, se reintenta"
                requeued.append(it.id)
            s.add(it)
        s.commit()
    return requeued, dead


def purge(*, now: datetime | None = None, days: int | None = None) -> int:
    """Borra trabajos terminados antiguos (los `dead` se conservan el doble: son lo que hay que revisar)."""
    now = now or utcnow()
    days = settings.worker_retention_days if days is None else days
    with Session(engine) as s:
        n = s.execute(WorkItem.__table__.delete().where(and_(WorkItem.state == "done", col(WorkItem.finished_at) < now - timedelta(days=days)))).rowcount
        n += s.execute(WorkItem.__table__.delete().where(and_(WorkItem.state == "dead", col(WorkItem.finished_at) < now - timedelta(days=days * 2)))).rowcount
        s.commit()
    return n


def depth(now: datetime | None = None) -> dict:
    """Estado de la cola: conteo por estado y antigüedad del trabajo listo más viejo."""
    now = now or utcnow()
    with Session(engine) as s:
        counts = dict(s.exec(select(WorkItem.state, func.count()).group_by(WorkItem.state)).all())
        oldest = s.exec(select(func.min(WorkItem.run_after)).where(WorkItem.state == "queued", col(WorkItem.run_after) <= now)).one()
    lag = (now - (oldest if oldest.tzinfo else oldest.replace(tzinfo=now.tzinfo))).total_seconds() if oldest else 0.0
    return {"queued": counts.get("queued", 0), "running": counts.get("running", 0), "dead": counts.get("dead", 0), "done": counts.get("done", 0),
            "oldest_ready_age_s": max(0.0, lag)}


def ready_count(now: datetime | None = None) -> int:
    """Trabajos en cola que ya pueden ejecutarse (los aplazados al futuro no cuentan)."""
    now = now or utcnow()
    with Session(engine) as s:
        return s.exec(select(func.count()).select_from(WorkItem).where(WorkItem.state == "queued", col(WorkItem.run_after) <= now)).one()
